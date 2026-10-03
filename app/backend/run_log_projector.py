"""Studio run log: StageRunner dispatches plus historical trajectory."""

from __future__ import annotations

from pathlib import Path

from ugc_harness.harness.models import ProjectState, TaskTrajectoryRecord

from .production_view import STAGE_LABELS
from .project_service import ProjectService
from .repository import AppDataRepository
from .agent_trace import (
    injected_context,
    merge_action_arguments,
    run_opening_messages,
    skill_name,
)
from .schemas import (
    IntakeTurnRecord,
    RunActionView,
    RunLogItem,
    RunLogRecord,
    RunLogView,
    StageName,
)


class RunLogProjector:
    def __init__(self, projects: ProjectService, repository: AppDataRepository) -> None:
        self.projects = projects
        self.repository = repository

    def view(self, project_dir: Path) -> RunLogView:
        state = self.projects.read_state(project_dir)
        return merge_run_log(
            state,
            self.repository.run_log(project_dir.name),
            project_key=project_dir.name,
            intake_turns=self.repository.intake_turns(project_dir.name),
            project_dir=project_dir,
        )


def empty_run_log(
    project_key: str | None = None,
    intake_turns: list[IntakeTurnRecord] | None = None,
) -> RunLogView:
    return RunLogView(project_key=project_key, intake_turns=intake_turns or [])


def merge_run_log(
    state: ProjectState,
    recorded: list[RunLogRecord],
    *,
    project_key: str,
    intake_turns: list[IntakeTurnRecord] | None = None,
    project_dir: Path | None = None,
) -> RunLogView:
    items: list[RunLogItem] = []
    seen: set[str] = set()
    for record in recorded:
        item = _from_record(record)
        if record.task_id:
            seen.add(record.task_id)
            _fill_from_trajectory(
                item, state, record.stage, record.task_id, project_dir=project_dir
            )
        else:
            _attach_trace(
                item,
                project_dir=project_dir,
                stage=item.stage,
                state=state,
            )
        items.append(item)
    for phase, trajectory in state.trajectory.phases.items():
        if phase not in STAGE_LABELS:
            continue
        stage: StageName = phase  # type: ignore[assignment]
        for record in trajectory.tasks:
            task_id = record.task.task_id
            if task_id in seen:
                continue
            seen.add(task_id)
            items.append(_from_trajectory(record, stage, project_dir=project_dir, state=state))
    items.sort(key=lambda item: item.created_at or item.finished_at or "")
    turns = list(intake_turns or [])
    turns.sort(key=lambda item: item.created_at or "")
    return RunLogView(project_key=project_key, items=items, intake_turns=turns)


def _from_record(record: RunLogRecord) -> RunLogItem:
    return RunLogItem(
        run_id=record.run_id,
        source=record.source,
        stage=record.stage,
        label=STAGE_LABELS[record.stage],
        agent=record.agent,
        goal=record.goal,
        instruction=record.instruction,
        status=record.status,
        task_id=record.task_id,
        allowed_tools=list(record.allowed_tools),
        target_refs=list(record.target_refs),
        shot_ids=list(record.shot_ids),
        actions=list(record.actions),
        critic_passed=record.critic_passed,
        error=record.error,
        received=record.received,
        lines=list(record.lines),
        context=dict(record.context),
        messages=list(record.messages),
        created_at=record.created_at,
        finished_at=record.finished_at,
        duration_ms=record.duration_ms,
    )


def _from_trajectory(
    record: TaskTrajectoryRecord,
    stage: StageName,
    *,
    project_dir: Path | None = None,
    state: ProjectState | None = None,
) -> RunLogItem:
    task = record.task
    result = record.agent_result
    return RunLogItem(
        run_id=f"traj:{task.task_id}",
        source="trajectory",
        stage=stage,
        label=STAGE_LABELS[stage],
        agent=task.agent,
        goal=task.goal,
        status=result.status,
        task_id=task.task_id,
        task_kind=record.task_kind,
        allowed_tools=list(task.allowed_tools),
        target_refs=list(task.scope.target_refs),
        shot_ids=list(task.scope.shot_ids),
        actions=_actions(result.actions),
        critic_passed=record.evaluation.passed,
        error=result.error,
        received=task.goal,
        context=injected_context(
            task, project_dir=project_dir, stage=stage, state=state
        ),
        messages=run_opening_messages(
            task, project_dir=project_dir, stage=stage, state=state
        ),
        created_at=record.recorded_at,
        finished_at=record.recorded_at,
    )


def _fill_from_trajectory(
    item: RunLogItem,
    state: ProjectState,
    stage: str,
    task_id: str,
    *,
    project_dir: Path | None = None,
) -> None:
    phase = state.trajectory.phases.get(stage)
    if phase is None:
        _attach_trace(
            item, project_dir=project_dir, stage=stage, state=state
        )
        return
    match = next(
        (record for record in reversed(phase.tasks) if record.task.task_id == task_id),
        None,
    )
    if match is None:
        _attach_trace(
            item, project_dir=project_dir, stage=stage, state=state
        )
        return
    if not item.actions:
        item.actions = _actions(match.agent_result.actions)
    if item.critic_passed is None:
        item.critic_passed = match.evaluation.passed
    if not item.goal:
        item.goal = match.task.goal
    if not item.task_kind:
        item.task_kind = match.task_kind
    if item.status in {"running", "completed"} and match.agent_result.status:
        if item.status != "failed":
            item.status = match.agent_result.status
    if not item.error:
        item.error = match.agent_result.error
    _attach_trace(
        item,
        task=match.task,
        project_dir=project_dir,
        stage=stage,
        state=state,
    )


def _attach_trace(
    item: RunLogItem,
    *,
    task: object | None = None,
    project_dir: Path | None = None,
    stage: str | None = None,
    state: ProjectState | None = None,
) -> None:
    if item.lines:
        item.actions = merge_action_arguments(item.actions, item.lines)
    item.context = injected_context(
        task,
        instruction=item.instruction,
        project_dir=project_dir,
        stage=stage,
        existing=item.context,
        state=state,
    )
    if not item.messages:
        item.messages = run_opening_messages(
            task,
            instruction=item.instruction,
            project_dir=project_dir,
            stage=stage,
            state=state,
            existing=item.context,
        )


def _actions(actions: list) -> list[RunActionView]:
    views: list[RunActionView] = []
    for item in actions:
        views.append(
            RunActionView(
                tool=getattr(item, "tool", "") or "",
                result=str(getattr(item, "result", "") or ""),
                duration_ms=int(getattr(item, "duration_ms", 0) or 0),
                reason=getattr(item, "reason", None),
                skill=skill_name(getattr(item, "tool", "") or "", None),
            )
        )
    return views
