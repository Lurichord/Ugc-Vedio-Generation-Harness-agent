from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..editorial_agent.models import ExplorationDirection, VisualRequirement
from ..voice_agent.models import RealizedBeat
from .models import (
    AssetCard,
    DirectionAttempt,
    VisualResolution,
)


class AcquisitionResult(Protocol):
    asset: AssetCard | None
    status: str
    reason: str


class AssetProvider(Protocol):
    def acquire(
        self,
        *,
        project_id: str,
        visual_request_id: str,
        beat: RealizedBeat,
        direction: ExplorationDirection,
        project_dir: Path,
        character_id: str | None = None,
        character_description: str | None = None,
        continuity_group_id: str | None = None,
        previous_character_asset: AssetCard | None = None,
        **kwargs: object,
    ) -> AcquisitionResult: ...


class AssetCapabilities:
    def __init__(self, provider: AssetProvider):
        self.provider = provider

    def acquire_requirement(
        self,
        *,
        project_id: str,
        visual: VisualRequirement,
        beat: RealizedBeat,
        project_dir: str | Path,
        character_description: str | None = None,
        continuity_group_id: str | None = None,
        previous_character_asset: AssetCard | None = None,
    ) -> tuple[AssetCard | None, VisualResolution]:
        root = Path(project_dir)
        attempts: list[DirectionAttempt] = []
        selected: AssetCard | None = None
        for direction in visual.directions:
            result = self.provider.acquire(
                project_id=project_id,
                visual_request_id=visual.visual_request_id,
                beat=beat,
                direction=direction,
                project_dir=root,
                character_id=visual.character_id,
                character_description=character_description,
                continuity_group_id=continuity_group_id,
                previous_character_asset=previous_character_asset,
            )
            attempts.append(
                DirectionAttempt(
                    direction_id=direction.direction_id,
                    order=direction.order,
                    status=result.status,
                    reason=result.reason,
                )
            )
            if result.asset is not None:
                selected = result.asset
                break
        return selected, VisualResolution(
            visual_request_id=visual.visual_request_id,
            beat_id=visual.beat_id,
            status="resolved" if selected else "unresolved",
            selected_direction_id=selected.direction_id if selected else None,
            asset_id=selected.asset_id if selected else None,
            attempts=attempts,
        )

    def generate_talking_head(
        self,
        *,
        project_id: str,
        visual: VisualRequirement,
        beat: RealizedBeat,
        project_dir: str | Path,
        prompt: str,
        references: list[dict[str, str]] | None,
        reference_audio_path: str | Path,
        character_description: str | None = None,
        continuity_group_id: str | None = None,
        previous_character_asset: AssetCard | None = None,
    ) -> tuple[AssetCard | None, VisualResolution]:
        talking = next(
            (
                direction
                for direction in visual.directions
                if direction.asset_type == "talking_head"
            ),
            None,
        )
        if talking is None:
            raise RuntimeError("该 VisualRequirement 没有 talking_head 方向")
        result = self.provider.acquire(
            project_id=project_id,
            visual_request_id=visual.visual_request_id,
            beat=beat,
            direction=talking,
            project_dir=Path(project_dir),
            character_id=visual.character_id,
            character_description=character_description,
            continuity_group_id=continuity_group_id,
            previous_character_asset=previous_character_asset,
            generation_prompt=prompt,
            generation_references=references,
            reference_audio_path=str(reference_audio_path),
        )
        selected = result.asset
        return selected, VisualResolution(
            visual_request_id=visual.visual_request_id,
            beat_id=visual.beat_id,
            status="resolved" if selected else "unresolved",
            selected_direction_id=selected.direction_id if selected else None,
            asset_id=selected.asset_id if selected else None,
            attempts=[
                DirectionAttempt(
                    direction_id=talking.direction_id,
                    order=talking.order,
                    status=result.status,
                    reason=result.reason,
                )
            ],
        )
