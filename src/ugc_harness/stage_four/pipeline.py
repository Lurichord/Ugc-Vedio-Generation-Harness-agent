from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..stage_three.models import EditorialStageArtifact, ExplorationDirection
from ..stage_two.models import RealizedBeat
from .models import (
    AssetCard,
    AssetQuality,
    AssetStageArtifact,
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
    ) -> AcquisitionResult: ...


class AssetAcquisitionPipeline:
    def __init__(self, provider: AssetProvider):
        self.provider = provider

    def run(
        self,
        stage_three: EditorialStageArtifact,
        realized_beats: list[RealizedBeat],
        project_dir: str | Path,
    ) -> AssetStageArtifact:
        root = Path(project_dir)
        beats = {beat.beat_id: beat for beat in realized_beats}
        assets: list[AssetCard] = []
        resolutions: list[VisualResolution] = []

        for visual in stage_three.editorial_plan.visual_requirements:
            beat = beats.get(visual.beat_id)
            if beat is None:
                raise ValueError(
                    f"VisualRequirement references unknown beat: {visual.beat_id}"
                )
            attempts: list[DirectionAttempt] = []
            selected: AssetCard | None = None
            for direction in visual.directions:
                result = self.provider.acquire(
                    project_id=stage_three.project_id,
                    visual_request_id=visual.visual_request_id,
                    beat=beat,
                    direction=direction,
                    project_dir=root,
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
                    assets.append(selected)
                    break

            resolutions.append(
                VisualResolution(
                    visual_request_id=visual.visual_request_id,
                    beat_id=visual.beat_id,
                    status="resolved" if selected else "unresolved",
                    selected_direction_id=(
                        selected.direction_id if selected else None
                    ),
                    asset_id=selected.asset_id if selected else None,
                    attempts=attempts,
                )
            )

        quality = evaluate_asset_stage(root, resolutions, assets)
        return AssetStageArtifact(
            project_id=stage_three.project_id,
            assets=assets,
            resolutions=resolutions,
            quality=quality,
        )


def evaluate_asset_stage(
    project_dir: Path,
    resolutions: list[VisualResolution],
    assets: list[AssetCard],
) -> AssetQuality:
    issues: list[str] = []
    violations = 0
    for resolution in resolutions:
        success_indexes = [
            index
            for index, attempt in enumerate(resolution.attempts)
            if attempt.status == "success"
        ]
        if success_indexes and success_indexes[0] != len(resolution.attempts) - 1:
            violations += 1
        if resolution.status == "unresolved":
            issues.append(f"{resolution.visual_request_id} 未找到可用素材")
    for asset in assets:
        path = project_dir / asset.local_path
        if not path.is_file() or path.stat().st_size == 0:
            issues.append(f"{asset.asset_id} 的本地文件不存在或为空")

    resolved = sum(item.status == "resolved" for item in resolutions)
    total = len(resolutions)
    coverage = resolved / total if total else 1.0
    if violations:
        issues.append(f"发现 {violations} 个 first-success 执行违规")
    return AssetQuality(
        passed=coverage == 1 and violations == 0 and not any(
            "不存在或为空" in issue for issue in issues
        ),
        visual_request_count=total,
        resolved_count=resolved,
        unresolved_count=total - resolved,
        resolution_coverage=round(coverage, 4),
        first_success_violations=violations,
        issues=issues,
    )
