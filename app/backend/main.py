from __future__ import annotations

import mimetypes
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .beat_projector import BeatProjector, stage_refs
from .config import AppConfig
from .intake_service import IntakeService
from .intake_store import IntakeStore
from .intake_workspace import IntakeWorkspaceStore
from .jobs import JobManager
from .production_view import ProductionView
from .project_service import ProjectService, STAGES
from .repair_service import RepairService
from .repository import AppDataRepository
from .review_service import ReviewService
from .schemas import (
    ApprovalRequest,
    AttachProjectRequest,
    CreateProjectRequest,
    FeedbackRequest,
    IntakeMessageRequest,
    RunStageRequest,
    StageName,
    TaskEvent,
)
from .stage_runner import StageRunner
from .task_projector import TaskProjector
from .timeline_projector import TimelineProjector


config = AppConfig.default()
repository = AppDataRepository(config.data_root)
projects = ProjectService(config.output_root, repository)
reviews = ReviewService(repository)
beats = BeatProjector(projects, reviews)
tasks = TaskProjector(projects, repository)
timeline = TimelineProjector(projects)
runner = StageRunner(config.output_root)
repairs = RepairService(projects, repository, runner)
jobs = JobManager()
production = ProductionView(projects, beats)
intake = IntakeService(
    IntakeStore(config.intake_root),
    IntakeWorkspaceStore(config.intake_root),
    runner,
    projects,
    reviews,
    beats,
    production,
    jobs,
    repository,
)

app = FastAPI(title="UGC Beat Studio", version="1.0.0")


def project_or_404(project_key: str) -> Path:
    try:
        return projects.project_dir(project_key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def valid_stage(stage: str) -> StageName:
    if stage not in STAGES:
        raise HTTPException(status_code=404, detail="unknown stage")
    return stage  # type: ignore[return-value]


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/intake/sessions", status_code=201)
def create_intake_session():
    return intake.create_session()


@app.get("/api/intake/sessions/{session_id}")
def get_intake_session(session_id: str):
    try:
        return intake.session_payload(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="intake session not found") from exc


@app.post("/api/intake/sessions/{session_id}/messages")
def post_intake_message(session_id: str, request: IntakeMessageRequest):
    try:
        return intake.handle_message(session_id, request.message)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="intake session not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/intake/sessions/{session_id}/continue")
def continue_intake_production(session_id: str):
    try:
        return intake.continue_production(session_id, user_text="可以")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="intake session not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/intake/sessions/{session_id}/attach")
def attach_intake_project(session_id: str, request: AttachProjectRequest):
    try:
        return intake.attach_project(session_id, request.project_key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="intake session or project not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/projects")
def list_projects():
    return projects.list_projects()


@app.post("/api/projects", status_code=202)
def create_project(request: CreateProjectRequest):
    def operation():
        directory = runner.create_project(request)
        state = projects.read_state(directory)
        repository.add_event(
            directory.name,
            TaskEvent(
                event_id=f"event_{uuid4().hex}",
                project_id=state.video.project_id,
                stage="narrative",
                event_type="awaiting_review",
                message="Narrative 已生成，等待用户审核",
            ),
        )
        return {"project_id": state.video.project_id, "project_key": directory.name}

    return jobs.submit("创建项目并生成 Narrative", operation)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    try:
        return jobs.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc


@app.get("/api/projects/{project_key}/stages/{stage}")
def stage_view(project_key: str, stage: str):
    directory = project_or_404(project_key)
    return beats.stage_view(directory, valid_stage(stage))


@app.post("/api/projects/{project_key}/stages/{stage}/run", status_code=202)
def run_stage(project_key: str, stage: str, request: RunStageRequest):
    stage_name = valid_stage(stage)
    directory = project_or_404(project_key)
    project_id = projects.read_state(directory).video.project_id
    view = beats.stage_view(directory, stage_name)
    if not view.can_run:
        raise HTTPException(status_code=409, detail="上一阶段尚未获得用户批准")

    def operation():
        started = time.monotonic()
        repository.add_event(project_key, TaskEvent(
            event_id=f"event_{uuid4().hex}", project_id=project_id,
            stage=stage_name, event_type="started", message=f"开始执行 {stage_name}",
        ))
        try:
            runner.run(stage_name, directory, request)
        except Exception:
            repository.add_event(project_key, TaskEvent(
                event_id=f"event_{uuid4().hex}", project_id=project_id,
                stage=stage_name, event_type="failed",
                duration_ms=int((time.monotonic() - started) * 1000),
            ))
            raise
        repository.add_event(project_key, TaskEvent(
            event_id=f"event_{uuid4().hex}", project_id=project_id,
            stage=stage_name, event_type="awaiting_review",
            duration_ms=int((time.monotonic() - started) * 1000),
            message=f"{stage_name} 已生成，等待用户审核",
        ))
        return {"project_id": project_id, "stage": stage_name}

    return jobs.submit(f"执行 {stage_name}", operation)


@app.post("/api/projects/{project_key}/stages/{stage}/approve")
def approve_stage(project_key: str, stage: str, request: ApprovalRequest):
    stage_name = valid_stage(stage)
    directory = project_or_404(project_key)
    project_id = projects.read_state(directory).video.project_id
    state = projects.read_state(directory)
    view = beats.stage_view(directory, stage_name)
    if request.expected_state_version != state.video.state_version:
        raise HTTPException(status_code=409, detail="项目状态已变化，请刷新后重试")
    expected_beats = {item.beat_id for item in view.beats if item.artifacts}
    if set(request.beat_ids) != expected_beats:
        raise HTTPException(status_code=422, detail="必须审核当前阶段的全部 Beat")
    if not view.can_approve:
        raise HTTPException(status_code=409, detail="Critic 未通过或仍有未解决反馈")
    record = reviews.approve(
        project_key, stage_name, state, request.beat_ids, stage_refs(view)
    )
    repository.add_event(project_key, TaskEvent(
        event_id=f"event_{uuid4().hex}", project_id=project_id,
        stage=stage_name, event_type="user_approved",
        beat_ids=request.beat_ids, target_refs=list(record.approved_refs),
        message=f"用户批准 {stage_name}",
    ))
    return record


@app.post("/api/projects/{project_key}/feedback")
def add_feedback(project_key: str, request: FeedbackRequest):
    directory = project_or_404(project_key)
    state = projects.read_state(directory)
    project_id = state.video.project_id
    view = beats.stage_view(directory, request.stage)
    valid_target = any(
        beat.beat_id == request.beat_id
        and any(item.ref == request.target_ref for item in beat.artifacts)
        for beat in view.beats
    )
    if not valid_target:
        raise HTTPException(
            status_code=422,
            detail="目标产物不属于所选阶段和 Beat",
        )
    try:
        record = reviews.add_feedback(project_key, state, request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    repository.add_event(project_key, TaskEvent(
        event_id=f"event_{uuid4().hex}", project_id=project_id,
        stage=request.stage, event_type="feedback_created",
        beat_ids=[request.beat_id], target_refs=[request.target_ref],
        message=request.instruction,
    ))
    return record


@app.get("/api/projects/{project_key}/feedback")
def list_feedback(project_key: str):
    project_or_404(project_key)
    return repository.feedback(project_key)


@app.post("/api/projects/{project_key}/repairs/{feedback_id}", status_code=202)
def run_repair(project_key: str, feedback_id: str):
    directory = project_or_404(project_key)
    project_id = projects.read_state(directory).video.project_id

    def operation():
        started = time.monotonic()
        repairs.run(directory, feedback_id)
        repository.add_event(project_key, TaskEvent(
            event_id=f"event_{uuid4().hex}", project_id=project_id,
            stage=next(item.stage for item in repository.feedback(project_key) if item.feedback_id == feedback_id),
            event_type="repair_completed",
            duration_ms=int((time.monotonic() - started) * 1000),
            message="局部修复完成，等待重新审核",
        ))
        return {"project_id": project_id, "feedback_id": feedback_id}

    return jobs.submit("执行局部修复", operation)


@app.get("/api/projects/{project_key}/tasks")
def task_history(project_key: str):
    return tasks.view(project_or_404(project_key))


@app.get("/api/projects/{project_key}/timeline")
def unified_timeline(project_key: str):
    return timeline.view(project_or_404(project_key))


@app.get("/api/projects/{project_key}/media/{media_path:path}")
def media(project_key: str, media_path: str):
    directory = project_or_404(project_key).resolve()
    path = (directory / media_path).resolve()
    if directory not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="media not found")
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0])


@app.get("/api/projects/{project_key}/artifact/{artifact_path:path}")
def artifact(project_key: str, artifact_path: str):
    directory = project_or_404(project_key).resolve()
    path = (directory / artifact_path).resolve()
    if directory not in path.parents or not path.is_file() or path.suffix != ".json":
        raise HTTPException(status_code=404, detail="artifact not found")
    return FileResponse(path, media_type="application/json")


app.mount("/", StaticFiles(directory=config.static_root, html=True), name="frontend")


def main() -> None:
    import uvicorn

    uvicorn.run("app.backend.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
