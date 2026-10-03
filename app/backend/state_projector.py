"""Studio view of ProjectState planes. App-only; no hashes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ugc_harness.harness.models import ProjectState

from .production_view import STAGE_LABELS
from .project_service import (
    STAGES,
    ProjectService,
    pipeline_format_id,
    production_pipeline,
)
from .schemas import (
    ProjectStateGraphView,
    ProjectStateProductionView,
    ProjectStateStageView,
    ProjectStateView,
    StageName,
)


class StateProjector:
    def __init__(self, projects: ProjectService) -> None:
        self.projects = projects

    def view(self, project_dir: Path) -> ProjectStateView:
        state = self.projects.read_state(project_dir)
        return project_state_view(
            state,
            project_key=project_dir.name,
            project_dir=project_dir,
            current_stage=self.projects.current_stage(state, project_dir.name),
        )


def empty_project_state_view(project_key: str | None = None) -> ProjectStateView:
    return ProjectStateView(project_key=project_key)


def project_state_view(
    state: ProjectState,
    *,
    project_key: str,
    project_dir: Path | None = None,
    current_stage: StageName | None = None,
) -> ProjectStateView:
    pipeline = list(production_pipeline(state, project_dir))
    format_id = pipeline_format_id(state, project_dir)
    topic, promise = _creative_copy(state)
    return ProjectStateView(
        project_key=project_key,
        schema_version=state.schema_version,
        project_id=state.context.project_id or None,
        state_version=state.context.state_version,
        format_id=format_id,
        topic=topic,
        promise=promise,
        current_stage=current_stage,
        pipeline=pipeline,
        stages=[
            _stage_view(state, stage, pipeline, current_stage) for stage in STAGES
        ],
        production=_production_view(state),
        graph=_graph_view(state),
        planes=_planes(state),
    )


def _creative_copy(state: ProjectState) -> tuple[str, str]:
    brief = state.creative.brief
    intent = state.creative.intent
    topic = ""
    promise = ""
    if intent is not None:
        topic = intent.topic or ""
        promise = intent.promise or intent.one_sentence_thesis or ""
    if brief is not None and not topic:
        topic = brief.topic or ""
    return topic, promise


def _stage_view(
    state: ProjectState,
    stage: StageName,
    pipeline: list[StageName],
    current_stage: StageName | None,
) -> ProjectStateStageView:
    required = stage in pipeline
    status = str(getattr(state.evaluation.stages, stage))
    if required and status == "not_required":
        status = "pending"
    if not required:
        status = "not_required"
    phase = state.trajectory.phases.get(stage)
    tasks = list(phase.tasks) if phase is not None else []
    last = tasks[-1] if tasks else None
    return ProjectStateStageView(
        stage=stage,
        label=STAGE_LABELS[stage],
        status=status,
        required=required,
        current=required and stage == current_stage,
        task_count=len(tasks),
        last_goal=last.task.goal if last is not None else None,
        last_status=last.agent_result.status if last is not None else None,
        critic_passed=last.evaluation.passed if last is not None else None,
    )


def _production_view(state: ProjectState) -> ProjectStateProductionView:
    world = state.production.world
    structure = state.production.structure
    kind = getattr(structure, "kind", None) if structure is not None else None
    units = 0
    if structure is not None:
        field = {"drama": "scenes", "tutorial": "steps", "explainer": "sections"}.get(kind)
        items = getattr(structure, field, None) if field else None
        if not items:
            for fallback in ("scenes", "steps", "beats", "sections"):
                items = getattr(structure, fallback, None)
                if items:
                    break
        units = len(items) if items else 0
    deliverable = state.production.deliverable
    return ProjectStateProductionView(
        cast=len(world.cast),
        props=len(world.props),
        places=len(world.places),
        shots=len(state.production.shots),
        structure_kind=kind,
        structure_units=units,
        has_timeline=state.production.timeline is not None,
        has_deliverable=bool(deliverable and deliverable.video_file),
    )


def _graph_view(state: ProjectState) -> ProjectStateGraphView:
    nodes = state.dependency_graph.nodes
    return ProjectStateGraphView(
        graph_version=state.dependency_graph.graph_version,
        node_count=len(nodes),
        stale_count=sum(1 for node in nodes.values() if node.status == "stale"),
    )


_HASH_KEYS = {"content_hash", "dependency_hashes", "input_hash", "sha256"}
_MEDIA_KEYS = {
    "reference_image",
    "local_path",
    "playback_path",
    "audio_file",
    "video_file",
    "cover_path",
    "file",
    "output_path",
}


def _planes(state: ProjectState) -> dict[str, Any]:
    return {
        "schema_version": state.schema_version,
        "context": _sanitize(state.context.model_dump(mode="json")),
        "creative": _sanitize(state.creative.model_dump(mode="json")),
        "production": _sanitize(state.production.model_dump(mode="json")),
        "evaluation": _sanitize(state.evaluation.model_dump(mode="json")),
        "dependency_graph": _sanitize(state.dependency_graph.model_dump(mode="json")),
        "trajectory": _sanitize(state.trajectory.model_dump(mode="json")),
    }


def _sanitize(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {
            name: _sanitize(item, name)
            for name, item in value.items()
            if name not in _HASH_KEYS
        }
    if isinstance(value, list):
        return [_sanitize(item, key) for item in value]
    if isinstance(value, str) and key in _MEDIA_KEYS:
        return _media_id(value)
    return value


def _media_id(value: str) -> str:
    name = value.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    if "." in name and not name.startswith("."):
        return name.rsplit(".", 1)[0] or name
    return name
