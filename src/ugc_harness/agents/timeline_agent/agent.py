from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ...harness.models import AgentResult, ArtifactRef, StatePatch, TaskEnvelope
from ..asset_agent.models import AssetArtifact
from ..base import BaseAgent, failed_result
from ..editorial_agent.models import EditorialArtifact
from ..voice_agent.models import VoiceArtifact
from .models import TimelineArtifact, TimelineCandidate


class TimelineAgentExecution(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    candidate: TimelineCandidate | None = None
    result: AgentResult


class TimelineAgent(BaseAgent[TimelineAgentExecution]):
    name = "timeline_agent"
    COMPOSE_TOOL = "timeline.compose"

    def run(self, task: TaskEnvelope, **kwargs: object) -> TimelineAgentExecution:
        self.validate_task(task)
        voice = kwargs.get("voice")
        editorial = kwargs.get("editorial")
        assets = kwargs.get("assets")
        project_dir = kwargs.get("project_dir")
        current = kwargs.get("current_artifact")
        if not isinstance(voice, VoiceArtifact):
            raise TypeError("TimelineAgent requires a VoiceArtifact")
        if not isinstance(editorial, EditorialArtifact):
            raise TypeError("TimelineAgent requires an EditorialArtifact")
        if not isinstance(assets, AssetArtifact):
            raise TypeError("TimelineAgent requires an AssetArtifact")
        if not isinstance(project_dir, (str, Path)):
            raise TypeError("TimelineAgent requires a project directory")
        if current is not None and not isinstance(current, TimelineArtifact):
            raise TypeError("current_artifact must be a TimelineArtifact")
        if task.scope.target_refs and current is None:
            raise ValueError("timeline repair requires the current TimelineArtifact")

        actions = []
        try:
            candidate = self.invoke_tool(
                task,
                actions,
                self.COMPOSE_TOOL,
                voice=voice,
                editorial=editorial,
                assets_artifact=assets,
                project_dir=project_dir,
            )
            if not isinstance(candidate, TimelineCandidate):
                raise TypeError("timeline.compose returned an invalid artifact")
            if task.scope.target_refs:
                candidate = _merge_scoped(candidate, current, set(task.scope.beat_ids))
        except Exception as exc:
            return TimelineAgentExecution(result=failed_result(task, actions, exc))
        return TimelineAgentExecution(
            candidate=candidate,
            result=AgentResult(
                task_id=task.task_id,
                status="completed",
                state_version_used=task.based_on_state_version,
                input_hash=task.input_hash,
                actions=actions,
                artifact_refs=[ArtifactRef(kind="timeline_candidate", id=voice.project_id)],
                state_patch=StatePatch(
                    set={"video.timeline_status": "ready"},
                    invalidate=["render:final"],
                ),
                evaluation_target=(
                    f"timeline:{voice.project_id}@{task.based_on_state_version + 1}"
                ),
            ),
        )


def _merge_scoped(
    candidate: TimelineCandidate,
    current: TimelineArtifact | None,
    beat_ids: set[str],
) -> TimelineCandidate:
    assert current is not None
    if not beat_ids:
        return candidate
    new_clips = {item.beat_id: item for item in candidate.timeline.clips}
    clips = [new_clips.get(item.beat_id, item) if item.beat_id in beat_ids else item for item in current.timeline.clips]
    new_captions = [item for item in candidate.captions if item.beat_id in beat_ids]
    captions = [item for item in current.captions if item.beat_id not in beat_ids] + new_captions
    targeted_clip_ids = {item.clip_id for item in clips if item.beat_id in beat_ids}
    new_transforms = {item.clip_id: item for item in candidate.visual_transforms}
    transforms = [
        new_transforms.get(item.clip_id, item) if item.clip_id in targeted_clip_ids else item
        for item in current.visual_transforms
    ]
    overlays = [item for item in current.overlays if item.beat_id not in beat_ids] + [
        item for item in candidate.overlays if item.beat_id in beat_ids
    ]
    derivatives = [item for item in current.derivatives if item.beat_id not in beat_ids] + [
        item for item in candidate.derivatives if item.beat_id in beat_ids
    ]
    return candidate.model_copy(
        update={
            "derivatives": derivatives,
            "timeline": candidate.timeline.model_copy(update={"clips": clips}),
            "captions": sorted(captions, key=lambda item: (item.start_ms, item.cue_id)),
            "visual_transforms": transforms,
            "overlays": sorted(overlays, key=lambda item: (item.start_ms, item.overlay_id)),
        }
    )
