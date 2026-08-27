from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import Field, model_validator

from ..agents.narrative_agent.models import NarrativeArtifact
from ..shared.models import StrictModel
from .description_realization import apply_shot_timeline_realization
from .models import ActionRecord, ProjectState, TaskBudget, TaskEnvelope, TaskScope
from .shot_asset_controller import ShotAssetArtifact


class ShotTimelineClip(StrictModel):
    clip_id: str
    shot_id: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    playback_path: str
    modality: Literal["ai_video"] = "ai_video"
    audio_mode: Literal["embedded_in_video", "mixed"]
    preserve_source_audio: bool
    caption: str | None = None


class ShotTimelineArtifact(StrictModel):
    schema_version: str = "shot-timeline.v1"
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    project_id: str
    format_id: Literal["drama", "tutorial"]
    source_narrative: str = "narrative_artifact.json"
    source_assets: str = "shot_asset_artifact.json"
    duration_ms: int = Field(gt=0)
    clips: list[ShotTimelineClip] = Field(min_length=1)

    @model_validator(mode="after")
    def contiguous(self) -> "ShotTimelineArtifact":
        if self.clips[0].start_ms != 0:
            raise ValueError("shot timeline must start at zero")
        for current, following in zip(self.clips, self.clips[1:]):
            if current.end_ms != following.start_ms:
                raise ValueError("shot timeline clips must be contiguous")
        if self.clips[-1].end_ms != self.duration_ms:
            raise ValueError("shot timeline must cover its duration")
        return self


class ShotTimelineRun(StrictModel):
    task: TaskEnvelope
    actions: list[ActionRecord]
    committed_state_version: int = Field(ge=1)
    project_state: ProjectState


class ShotTimelineHarnessController:
    """Orders already generated clips; no Agent and no internal planning graph."""

    TOOL = "timeline.sequence_ai_video_shots"

    def run(
        self,
        narrative: NarrativeArtifact,
        assets: ShotAssetArtifact,
        state: ProjectState,
    ) -> tuple[ShotTimelineArtifact, ShotTimelineRun]:
        if narrative.shots is None:
            raise ValueError("shot timeline requires ProductionShots")
        if state.video.asset_status != "passed" or state.video.timeline_status != "ready":
            raise ValueError("shot timeline stage is not ready")
        if assets.project_id != narrative.brief.project_id:
            raise ValueError("timeline inputs have different project_id values")
        by_shot = {asset.shot_id: asset for asset in assets.assets}
        expected = [shot.shot_id for shot in narrative.shots.shots]
        if set(by_shot) != set(expected):
            raise ValueError("shot assets do not exactly cover Narrative shots")
        input_hash = hashlib.sha256(
            (narrative.model_dump_json() + "\n" + assets.model_dump_json()).encode()
        ).hexdigest()
        task = TaskEnvelope(
            task_id=f"task_shot_timeline_{assets.project_id}_v{state.video.state_version}",
            agent="timeline_stage",
            goal="Sequence AI video Shot assets in Narrative order without replacing their audio.",
            scope=TaskScope(project_id=assets.project_id, shot_ids=expected),
            based_on_state_version=state.video.state_version,
            format_id=assets.format_id,
            allowed_tools=[self.TOOL],
            required_outputs=["shot_timeline_artifact"],
            forbidden_actions=["replace_assets", "mute_source_audio", "modify_narrative"],
            budget=TaskBudget(max_steps=2, max_retries=0),
            input_hash=input_hash,
        )
        cursor = 0
        clips: list[ShotTimelineClip] = []
        for shot in narrative.shots.shots:
            asset = by_shot[shot.shot_id]
            end = cursor + asset.duration_ms
            clips.append(ShotTimelineClip(
                clip_id=f"clip_{shot.shot_id}",
                shot_id=shot.shot_id,
                start_ms=cursor,
                end_ms=end,
                playback_path=asset.local_path,
                audio_mode=asset.audio_mode,
                preserve_source_audio=asset.preserve_source_audio,
                caption=getattr(shot.payload, "narration_text", None),
            ))
            cursor = end
        artifact = ShotTimelineArtifact(
            project_id=assets.project_id,
            format_id=assets.format_id,
            duration_ms=cursor,
            clips=clips,
        )
        next_state = state.model_copy(deep=True)
        next_state.video.state_version += 1
        next_state.video.timeline_status = "passed"
        next_state.video.render_status = "ready"
        apply_shot_timeline_realization(next_state, artifact)
        action = ActionRecord(
            action_id=f"action_{uuid.uuid4().hex[:12]}",
            agent="timeline_stage",
            task_id=task.task_id,
            tool=self.TOOL,
            result="success",
            reason=f"sequenced {len(clips)} native-audio AI video clips",
        )
        return artifact, ShotTimelineRun(
            task=task,
            actions=[action],
            committed_state_version=next_state.video.state_version,
            project_state=next_state,
        )
