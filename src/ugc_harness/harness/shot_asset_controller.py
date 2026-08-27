from __future__ import annotations

import hashlib
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Protocol

from pydantic import Field

from ..agents.narrative_agent.models import NarrativeArtifact
from ..content import ProductionShot
from ..shared.models import StrictModel
from .description_realization import apply_shot_media
from .models import ActionRecord, ProjectState, TaskBudget, TaskEnvelope, TaskScope
from .production_routes import resolve_production_route


class GeneratedShotVideo(StrictModel):
    content: bytes
    mime_type: str = "video/mp4"
    model: str
    prompt: str
    job_id: str | None = None
    duration_ms: int = Field(gt=0)
    cost_usd: float | None = Field(default=None, ge=0)


class ShotVideoProvider(Protocol):
    def generate(self, shot: ProductionShot, *, progress_path: Path) -> GeneratedShotVideo: ...


class ShotVideoAsset(StrictModel):
    asset_id: str
    shot_id: str
    modality: Literal["ai_video"] = "ai_video"
    local_path: str
    mime_type: str
    sha256: str
    generator_model: str
    generation_prompt: str
    generation_job_id: str | None = None
    duration_ms: int = Field(gt=0)
    generation_cost_usd: float | None = Field(default=None, ge=0)
    audio_mode: Literal["embedded_in_video", "mixed"]
    preserve_source_audio: bool
    generated_media_disclosure_required: Literal[True] = True


class ShotAssetQuality(StrictModel):
    passed: bool
    shot_count: int = Field(ge=1)
    ai_video_count: int = Field(ge=1)
    missing_shot_ids: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class ShotAssetArtifact(StrictModel):
    schema_version: str = "shot-assets.v1"
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    project_id: str
    format_id: Literal["drama", "tutorial"]
    source_narrative: str = "narrative_artifact.json"
    assets: list[ShotVideoAsset] = Field(min_length=1)
    quality: ShotAssetQuality


class ShotAssetRun(StrictModel):
    task: TaskEnvelope
    actions: list[ActionRecord]
    committed_state_version: int = Field(ge=1)
    project_state: ProjectState


class ShotAssetHarnessController:
    """A deterministic Harness stage; this is intentionally not an Agent."""

    TOOL = "asset.generate_ai_video"

    def __init__(self, provider: ShotVideoProvider) -> None:
        self.provider = provider

    def create_task(self, narrative: NarrativeArtifact, state: ProjectState) -> TaskEnvelope:
        route = resolve_production_route(narrative)
        if route.asset_route != "shot_ai_video" or narrative.shots is None:
            raise ValueError("Shot Asset route only supports drama/tutorial AI video")
        return TaskEnvelope(
            task_id=f"task_shot_asset_{narrative.brief.project_id}_v{state.video.state_version}",
            agent="asset_stage",
            goal="Generate exactly one AI video asset for every ProductionShot.",
            scope=TaskScope(
                project_id=narrative.brief.project_id,
                shot_ids=[shot.shot_id for shot in narrative.shots.shots],
            ),
            based_on_state_version=state.video.state_version,
            format_id=route.format_id,
            allowed_tools=[self.TOOL],
            required_outputs=["shot_asset_artifact"],
            forbidden_actions=[
                "search_web_assets",
                "generate_images",
                "read_editorial_artifact",
                "read_voice_artifact",
                "modify_narrative",
            ],
            acceptance_criteria=[
                "Every scoped Shot has exactly one ai_video asset.",
                "Drama keeps embedded clip audio; tutorial keeps source operation audio.",
            ],
            budget=TaskBudget(
                max_steps=min(32, max(2, len(narrative.shots.shots))),
                max_retries=1,
            ),
            input_hash=hashlib.sha256(narrative.model_dump_json().encode()).hexdigest(),
        )

    def run(
        self,
        narrative: NarrativeArtifact,
        project_dir: str | Path,
        state: ProjectState,
        task: TaskEnvelope | None = None,
    ) -> tuple[ShotAssetArtifact, ShotAssetRun]:
        route = resolve_production_route(narrative)
        if route.asset_route != "shot_ai_video" or narrative.shots is None:
            raise ValueError("Shot Asset route only supports drama/tutorial AI video")
        if state.video.project_id != narrative.brief.project_id:
            raise ValueError("asset inputs have different project_id values")
        if state.video.asset_status not in {"ready", "needs_revision", "stale"}:
            raise ValueError("shot asset stage is not ready in project state")
        envelope = task or self.create_task(narrative, state)
        if envelope.based_on_state_version != state.video.state_version:
            raise ValueError("STALE_RESULT: shot asset task uses an obsolete state")
        expected_hash = hashlib.sha256(narrative.model_dump_json().encode()).hexdigest()
        if envelope.input_hash != expected_hash:
            raise ValueError("shot asset task input_hash does not match Narrative")

        root = Path(project_dir)
        folder = root / "assets" / "generated_shots"
        folder.mkdir(parents=True, exist_ok=True)
        assets: list[ShotVideoAsset] = []
        actions: list[ActionRecord] = []
        for shot in narrative.shots.shots:
            started = time.monotonic()
            progress = root / "harness" / "seedance_jobs" / f"{shot.shot_id}.json"
            try:
                generated = self.provider.generate(shot, progress_path=progress)
                output = folder / f"asset_{shot.shot_id}.mp4"
                output.write_bytes(generated.content)
                preserve_source = (
                    shot.audio.audio_mode == "embedded_in_video"
                    or bool(getattr(shot.audio, "preserve_source_audio", False))
                )
                assets.append(ShotVideoAsset(
                    asset_id=f"asset_{shot.shot_id}",
                    shot_id=shot.shot_id,
                    local_path=output.relative_to(root).as_posix(),
                    mime_type=generated.mime_type,
                    sha256=hashlib.sha256(generated.content).hexdigest(),
                    generator_model=generated.model,
                    generation_prompt=generated.prompt,
                    generation_job_id=generated.job_id,
                    duration_ms=generated.duration_ms,
                    generation_cost_usd=generated.cost_usd,
                    audio_mode=shot.audio.audio_mode,
                    preserve_source_audio=preserve_source,
                ))
                result: Literal["success", "failed"] = "success"
                reason = None
            except Exception as exc:
                result = "failed"
                reason = str(exc)
                actions.append(self._action(envelope, shot, started, result, reason))
                raise
            actions.append(self._action(envelope, shot, started, result, reason))

        quality = ShotAssetQuality(
            passed=len(assets) == len(narrative.shots.shots),
            shot_count=len(narrative.shots.shots),
            ai_video_count=len(assets),
        )
        artifact = ShotAssetArtifact(
            project_id=narrative.brief.project_id,
            format_id=route.format_id,
            assets=assets,
            quality=quality,
        )
        next_state = state.model_copy(deep=True)
        next_state.video.state_version += 1
        next_state.video.asset_status = "passed"
        next_state.video.timeline_status = "ready"
        apply_shot_media(next_state, artifact)
        run = ShotAssetRun(
            task=envelope,
            actions=actions,
            committed_state_version=next_state.video.state_version,
            project_state=next_state,
        )
        return artifact, run

    @staticmethod
    def _action(
        task: TaskEnvelope,
        shot: ProductionShot,
        started: float,
        result: Literal["success", "failed"],
        reason: str | None,
    ) -> ActionRecord:
        return ActionRecord(
            action_id=f"action_{uuid.uuid4().hex[:12]}",
            agent="asset_stage",
            task_id=task.task_id,
            tool=ShotAssetHarnessController.TOOL,
            result=result,
            reason=reason or f"generated ai_video for {shot.shot_id}",
            duration_ms=round((time.monotonic() - started) * 1000),
        )
