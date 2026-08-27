from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from ugc_harness.intake.agent import IntentAgent
from ugc_harness.intake.brief_sync import ChainedBriefSync, HeuristicBriefSync, LlmBriefSync
from ugc_harness.intake.host import IntentHost, new_session
from ugc_harness.intake.mcp_runtime import IntakeMcpRuntime
from ugc_harness.intake.models import Inbound, IntakeSession
from ugc_harness.intake.view import refresh_session_from_disk
from ugc_harness.shared.llm import StructuredLLM
from ugc_harness.shared.settings import LLMSettings

from .beat_projector import BeatProjector, stage_refs
from .intake_store import IntakeStore
from .intake_workspace import IntakeWorkspaceStore, set_gate
from .jobs import JobManager
from .production_view import (
    STAGE_LABELS,
    ProductionView,
    gate_question,
    next_stage_after,
)
from .project_service import STAGES, ProjectService
from .review_service import ReviewService
from .schemas import (
    CreateIntakeSessionResponse,
    IntakeMessageResponse,
    IntakeWorkspaceState,
    PendingGate,
    RunStageRequest,
    StageName,
    TaskEvent,
)
from .stage_runner import StageRunner

_CONTINUE = re.compile(
    r"^(可以|继续|好的?|行|没问题|一样|下一步|开始吧|做吧|ok|yes|y)"
    r"([，,。.!！ ]*(继续|下一步|开始|做吧|一样)?)?$",
    re.IGNORECASE,
)
_REVISE = re.compile(r"(改|重做|不对|不好|太|缩短|加长|重来|修)")


class IntakeService:
    def __init__(
        self,
        store: IntakeStore,
        workspace: IntakeWorkspaceStore,
        runner: StageRunner,
        projects: ProjectService,
        reviews: ReviewService,
        beats: BeatProjector,
        production: ProductionView,
        jobs: JobManager,
        repository,
        agent: IntentAgent | None = None,
    ) -> None:
        self.store = store
        self.workspace = workspace
        self.runner = runner
        self.projects = projects
        self.reviews = reviews
        self.beats = beats
        self.production = production
        self.jobs = jobs
        self.repository = repository
        self._agent = agent
        self._llm: StructuredLLM | None = None

    def create_session(self) -> CreateIntakeSessionResponse:
        session = new_session()
        self.store.save(session)
        self.workspace.save(IntakeWorkspaceState(session_id=session.session_id))
        return self.session_payload(session.session_id)

    def get_session(self, session_id: str) -> IntakeSession:
        return self.store.load(session_id)

    def session_payload(self, session_id: str) -> CreateIntakeSessionResponse:
        session = self.store.load(session_id)
        state = self._sync_workspace(session)
        return CreateIntakeSessionResponse(**self._payload(session, state))

    def handle_message(self, session_id: str, message: str) -> IntakeMessageResponse:
        session = self.store.load(session_id)
        state = self._sync_workspace(session)
        text = message.strip()
        gate = state.pending_gate
        if gate and gate.kind == "running":
            state = self.workspace.add_notice(state, text, role="user")
            state = self.workspace.add_notice(
                state,
                f"{STAGE_LABELS.get(gate.stage or 'narrative', '制作')}还在跑，先看右侧进度。",
            )
            return self._turn_payload(session, state, state.notices[-1].content, job_id=gate.job_id)
        if gate and gate.kind == "review" and _wants_continue(text):
            return self.continue_production(session_id, user_text=text)
        result = self._host(session_id).respond(session, Inbound(text=text))
        self.store.save(result.session)
        state = self._sync_workspace(result.session)
        reply = result.message
        if state.pending_gate and state.pending_gate.kind == "review":
            if not any(item.content == state.pending_gate.question for item in state.notices):
                state = self.workspace.add_notice(state, state.pending_gate.question)
            reply = f"{reply}\n\n{state.pending_gate.question}"
        return self._turn_payload(
            result.session,
            state,
            reply,
            done=result.done,
        )

    def continue_production(
        self,
        session_id: str,
        *,
        user_text: str | None = None,
    ) -> IntakeMessageResponse:
        session = self.store.load(session_id)
        state = self._sync_workspace(session)
        if user_text:
            state = self.workspace.add_notice(state, user_text, role="user")
        if not state.project_key:
            raise ValueError("还没有项目，不能进入下一阶段")
        gate = state.pending_gate
        if gate and gate.kind == "running":
            return self._turn_payload(
                session, state, "这一步还在跑。", job_id=gate.job_id
            )
        if gate and gate.kind == "done":
            state = self.workspace.add_notice(state, "成片已经在右侧了。")
            return self._turn_payload(session, state, state.notices[-1].content)
        directory = self.projects.project_dir(state.project_key)
        project_state = self.projects.read_state(directory)
        statuses = _statuses(project_state)
        stage = (gate.stage if gate else None) or self.projects.current_stage(
            project_state, state.project_key
        )
        view = self.beats.stage_view(directory, stage)
        if view.core_status in {"pending", "failed", "needs_revision", "stale", "blocked"}:
            return self._start_stage_job(session, state, directory, stage)
        if view.core_status in {"ready", "passed", "locked"} and not view.user_approved:
            if not view.critic_passed and view.issues:
                raise ValueError("这一步还不能批准：Critic 未通过")
            if self.reviews.open_feedback(state.project_key, stage):
                raise ValueError("还有未解决的反馈，先改这一步")
            beat_ids = [item.beat_id for item in view.beats if item.artifacts] or [
                item.beat_id for item in view.beats
            ] or ["all"]
            self.reviews.approve(
                state.project_key,
                stage,
                project_state,
                beat_ids,
                stage_refs(view),
            )
            self.repository.add_event(
                state.project_key,
                TaskEvent(
                    event_id=f"event_{uuid4().hex}",
                    project_id=project_state.video.project_id,
                    stage=stage,
                    event_type="user_approved",
                    beat_ids=beat_ids,
                    message=f"用户确认 {stage}",
                ),
            )
        nxt = next_stage_after(stage, statuses)
        if nxt is None:
            question = gate_question(stage, None)
            state = set_gate(
                state,
                PendingGate(kind="done", stage=stage, question=question),
            )
            self.workspace.save(state)
            state = self.workspace.add_notice(state, question)
            return self._turn_payload(session, state, question)
        return self._start_stage_job(session, state, directory, nxt)

    def attach_project(self, session_id: str, project_key: str) -> CreateIntakeSessionResponse:
        session = self.store.load(session_id)
        directory = self.projects.project_dir(project_key)
        project_state = self.projects.read_state(directory)
        session = session.model_copy(
            update={
                "project_dir": str(directory),
                "project_id": project_state.video.project_id,
            }
        )
        session = refresh_session_from_disk(session)
        self.store.save(session)
        state = self.workspace.load(session_id)
        state = state.model_copy(update={"project_key": project_key})
        state = self._apply_gate_from_project(state, directory)
        self.workspace.save(state)
        if state.pending_gate and state.pending_gate.kind == "review":
            if not any(item.content == state.pending_gate.question for item in state.notices):
                state = self.workspace.add_notice(state, state.pending_gate.question)
        return CreateIntakeSessionResponse(**self._payload(session, state))

    def _start_stage_job(
        self,
        session: IntakeSession,
        state: IntakeWorkspaceState,
        directory: Path,
        stage: StageName,
    ) -> IntakeMessageResponse:
        project_id = self.projects.read_state(directory).video.project_id
        label = STAGE_LABELS[stage]
        started = self.workspace.add_notice(state, f"开始做{label}，完成后会停下来问你。")
        job = self.jobs.submit(
            f"执行 {stage}",
            lambda sid=started.session_id, d=directory, s=stage, pid=project_id: (
                self._run_stage(sid, d, s, pid)
            ),
        )
        running = PendingGate(
            kind="running",
            stage=stage,
            question=f"{label}正在生成，右侧会陆续出现内容。",
            job_id=job["job_id"],
        )
        started = set_gate(started, running)
        self.workspace.save(started)
        return self._turn_payload(
            session,
            started,
            started.notices[-1].content,
            job_id=job["job_id"],
            harness_action=f"run_{stage}",
        )

    def _run_stage(
        self,
        session_id: str,
        directory: Path,
        stage: StageName,
        project_id: str,
    ) -> dict[str, Any]:
        self.repository.add_event(
            directory.name,
            TaskEvent(
                event_id=f"event_{uuid4().hex}",
                project_id=project_id,
                stage=stage,
                event_type="started",
                message=f"开始执行 {stage}",
            ),
        )
        try:
            self.runner.run(stage, directory, RunStageRequest())
        except Exception as exc:
            state = self.workspace.load(session_id)
            failed = PendingGate(
                kind="failed",
                stage=stage,
                question=f"{STAGE_LABELS[stage]}失败了：{exc}",
                error=str(exc),
            )
            state = set_gate(state, failed)
            self.workspace.save(state)
            self.workspace.add_notice(state, failed.question)
            self.repository.add_event(
                directory.name,
                TaskEvent(
                    event_id=f"event_{uuid4().hex}",
                    project_id=project_id,
                    stage=stage,
                    event_type="failed",
                    message=str(exc),
                ),
            )
            raise
        state = self.workspace.load(session_id)
        project_state = self.projects.read_state(directory)
        nxt = next_stage_after(stage, _statuses(project_state))
        question = gate_question(stage, nxt)
        state = set_gate(
            state,
            PendingGate(
                kind="review",
                stage=stage,
                next_stage=nxt,
                question=question,
            ),
        )
        self.workspace.save(state)
        self.workspace.add_notice(state, question)
        self.repository.add_event(
            directory.name,
            TaskEvent(
                event_id=f"event_{uuid4().hex}",
                project_id=project_id,
                stage=stage,
                event_type="awaiting_review",
                message=question,
            ),
        )
        return {"project_id": project_id, "stage": stage}

    def _sync_workspace(self, session: IntakeSession) -> IntakeWorkspaceState:
        state = self.workspace.load(session.session_id)
        project_key = state.project_key
        if session.project_dir:
            path = Path(session.project_dir)
            if path.is_dir():
                project_key = path.name
        if project_key != state.project_key:
            state = state.model_copy(update={"project_key": project_key})
            self.workspace.save(state)
        if project_key and (state.pending_gate is None or state.pending_gate.kind == "failed"):
            try:
                directory = self.projects.project_dir(project_key)
            except FileNotFoundError:
                return state
            state = self._apply_gate_from_project(state, directory)
            self.workspace.save(state)
        elif project_key and state.pending_gate and state.pending_gate.kind == "running":
            job_id = state.pending_gate.job_id
            if job_id:
                try:
                    job = self.jobs.get(job_id)
                except KeyError:
                    job = None
                if job and job["status"] == "completed":
                    directory = self.projects.project_dir(project_key)
                    state = self._apply_gate_from_project(state, directory)
                    self.workspace.save(state)
        return self.workspace.load(session.session_id)

    def _apply_gate_from_project(
        self,
        state: IntakeWorkspaceState,
        directory: Path,
    ) -> IntakeWorkspaceState:
        project_state = self.projects.read_state(directory)
        statuses = _statuses(project_state)
        current = self.projects.current_stage(project_state, directory.name)
        view = self.beats.stage_view(directory, current)
        if view.core_status == "running":
            return set_gate(
                state,
                PendingGate(
                    kind="running",
                    stage=current,
                    question=f"{STAGE_LABELS[current]}正在生成。",
                    job_id=state.pending_gate.job_id if state.pending_gate else None,
                ),
            )
        if view.core_status in {"ready", "passed", "locked"} and not view.user_approved:
            nxt = next_stage_after(current, statuses)
            return set_gate(
                state,
                PendingGate(
                    kind="review",
                    stage=current,
                    next_stage=nxt,
                    question=gate_question(current, nxt),
                ),
            )
        if view.core_status in {"pending", "failed", "needs_revision", "stale", "blocked"}:
            previous = _previous_required(current, statuses)
            if previous and self.reviews.valid_approval(
                directory.name, previous, project_state
            ):
                return set_gate(
                    state,
                    PendingGate(
                        kind="review",
                        stage=previous,
                        next_stage=current,
                        question=gate_question(previous, current),
                    ),
                )
        if current == "render" and view.user_approved:
            return set_gate(
                state,
                PendingGate(
                    kind="done",
                    stage="render",
                    question=gate_question("render", None),
                ),
            )
        return state

    def _payload(
        self,
        session: IntakeSession,
        state: IntakeWorkspaceState,
        **extra: Any,
    ) -> dict[str, Any]:
        reply = extra.get("reply")
        if reply is None:
            reply = session.last_message or ""
            if state.pending_gate and state.pending_gate.kind in {"review", "done"}:
                reply = state.pending_gate.question
        return {
            "session_id": session.session_id,
            "status": session.status,
            "reply": reply,
            "address": extra.get("address")
            or ("done" if session.status == "done" else "user"),
            "check_ok": extra.get("check_ok", True),
            "issues": extra.get("issues") or [],
            "project_id": session.project_id,
            "project_key": state.project_key,
            "job_id": extra.get("job_id")
            or (state.pending_gate.job_id if state.pending_gate else None),
            "harness_action": extra.get("harness_action"),
            "messages": [item.model_dump(mode="json") for item in session.messages],
            "brief": session.working_brief.model_dump(mode="json"),
            "intent": session.working_intent.model_dump(mode="json"),
            "progress": session.progress.model_dump(mode="json"),
            "production": self.production.snapshot(state.project_key).model_dump(
                mode="json"
            ),
            "gate": (
                state.pending_gate.model_dump(mode="json")
                if state.pending_gate
                else None
            ),
            "notices": [item.model_dump(mode="json") for item in state.notices],
        }

    def _turn_payload(
        self,
        session: IntakeSession,
        state: IntakeWorkspaceState,
        reply: str,
        *,
        done: bool = False,
        job_id: str | None = None,
        harness_action: str | None = None,
    ) -> IntakeMessageResponse:
        return IntakeMessageResponse(
            **self._payload(
                session,
                state,
                reply=reply,
                address="done" if done else "user",
                check_ok=True,
                job_id=job_id,
                harness_action=harness_action,
            )
        )

    def _host(self, session_id: str) -> IntentHost:
        llm = self._get_llm()
        return IntentHost(
            self._get_agent(),
            IntakeMcpRuntime(
                self.store.path_for(session_id),
                output_root=self.runner.output_root,
            ),
            brief_sync=ChainedBriefSync(HeuristicBriefSync(), LlmBriefSync(llm)),
        )

    def _get_agent(self) -> IntentAgent:
        if self._agent is None:
            self._agent = IntentAgent(self._get_llm())
        return self._agent

    def _get_llm(self) -> StructuredLLM:
        if self._llm is None:
            self._llm = StructuredLLM(LLMSettings.from_environment(None, None))
        return self._llm


def _wants_continue(text: str) -> bool:
    cleaned = text.strip()
    if _CONTINUE.fullmatch(cleaned):
        return True
    if _REVISE.search(cleaned):
        return False
    return cleaned in {"可以继续", "一样就行", "就这样", "不用改"}


def _statuses(project_state) -> dict[StageName, str]:
    return {
        stage: str(getattr(project_state.video, f"{stage}_status"))
        for stage in STAGES
    }


def _previous_required(
    stage: StageName, statuses: dict[StageName, str]
) -> StageName | None:
    pipeline = [item for item in STAGES if statuses.get(item) != "not_required"]
    try:
        index = pipeline.index(stage)
    except ValueError:
        return None
    if index == 0:
        return None
    return pipeline[index - 1]
