from __future__ import annotations

from pathlib import Path
from typing import Protocol

from PIL import Image

from ..agents.asset_agent.image_models import AssetInspection, PreparedImage
from ..agents.asset_agent.models import AssetCard, AssetQuality, VisualResolution
from ..agents.voice_agent.models import RealizedBeat, VoiceArtifact
from ..agents.editorial_agent.models import EditorialArtifact
from ..harness.models import CriticIssue, EvaluationResult


class ImageAnalyzer(Protocol):
    def analyze(
        self,
        *,
        asset: AssetCard,
        beat: RealizedBeat,
        image_path: Path,
    ) -> AssetInspection: ...


class AssetCritic:
    critic_id = "asset_critic"

    def __init__(self, image_analyzer: ImageAnalyzer) -> None:
        self.image_analyzer = image_analyzer

    def evaluate(
        self,
        project_dir: str | Path,
        resolutions: list[VisualResolution],
        assets: list[AssetCard],
        expected_visual_request_ids: list[str],
        target_ref: str,
        voice: VoiceArtifact,
        editorial: EditorialArtifact,
        prepared_images: list[PreparedImage] | None = None,
    ) -> tuple[AssetQuality, EvaluationResult, list[AssetInspection]]:
        root = Path(project_dir)
        issue_specs: list[tuple[str, str, str, list[str]]] = []
        violations = 0
        resolution_ids = [item.visual_request_id for item in resolutions]
        missing = set(expected_visual_request_ids) - set(resolution_ids)
        extra = set(resolution_ids) - set(expected_visual_request_ids)
        duplicates = {ref for ref in resolution_ids if resolution_ids.count(ref) > 1}
        if missing or extra or duplicates:
            _add(
                issue_specs,
                "ASSET_COVERAGE_INVALID",
                f"Invalid visual resolution coverage; missing={sorted(missing)}, "
                f"extra={sorted(extra)}, duplicates={sorted(duplicates)}.",
                target_ref,
                ["revise_visual_requirement"],
            )

        asset_ids = {item.asset_id for item in assets}
        for resolution in resolutions:
            success_indexes = [
                index
                for index, attempt in enumerate(resolution.attempts)
                if attempt.status == "success"
            ]
            if success_indexes and success_indexes[0] != len(resolution.attempts) - 1:
                violations += 1
            if resolution.status == "unresolved":
                _add(
                    issue_specs,
                    "ASSET_UNRESOLVED",
                    f"{resolution.visual_request_id} has no usable asset.",
                    f"visual_resolution:{resolution.visual_request_id}",
                    ["retry_next_direction", "revise_visual_requirement"],
                )
            if resolution.asset_id and resolution.asset_id not in asset_ids:
                _add(
                    issue_specs,
                    "ASSET_COVERAGE_INVALID",
                    f"{resolution.visual_request_id} references an unknown asset.",
                    f"visual_resolution:{resolution.visual_request_id}",
                    ["retry_next_direction"],
                )
        if violations:
            _add(
                issue_specs,
                "FIRST_SUCCESS_VIOLATION",
                f"Found {violations} first-success execution violations.",
                target_ref,
                ["retry_next_direction"],
            )

        beats = {item.beat_id: item for item in voice.realized_beats}
        assets_by_id = {item.asset_id: item for item in assets}
        resolutions_by_request = {
            item.visual_request_id: item for item in resolutions
        }
        previous_visual = None
        previous_aroll_asset: AssetCard | None = None
        identity_reference_by_character: dict[str, str] = {}
        last_group_by_character: dict[str, str] = {}
        for visual in editorial.editorial_plan.visual_requirements:
            resolution = resolutions_by_request.get(visual.visual_request_id)
            asset = (
                assets_by_id.get(str(resolution.asset_id))
                if resolution and resolution.asset_id
                else None
            )
            if visual.track == "a_roll" and asset is not None:
                if not asset.mime_type.startswith("video/"):
                    _add(
                        issue_specs,
                        "AROLL_NOT_DYNAMIC",
                        f"{asset.asset_id} is a static A-roll asset.",
                        f"asset:{asset.asset_id}",
                        ["regenerate_talking_head_video"],
                    )
                if asset.character_id != visual.character_id:
                    _add(
                        issue_specs,
                        "AROLL_CHARACTER_MISMATCH",
                        f"{asset.asset_id} does not match {visual.character_id}.",
                        f"asset:{asset.asset_id}",
                        ["regenerate_talking_head_video"],
                    )
                if not asset.identity_reference_path:
                    _add(
                        issue_specs,
                        "AROLL_IDENTITY_REFERENCE_MISSING",
                        f"{asset.asset_id} has no persistent identity reference.",
                        f"asset:{asset.asset_id}",
                        ["regenerate_talking_head_video"],
                    )
                elif visual.character_id:
                    expected_reference = identity_reference_by_character.setdefault(
                        visual.character_id, asset.identity_reference_path
                    )
                    if expected_reference != asset.identity_reference_path:
                        _add(
                            issue_specs,
                            "AROLL_IDENTITY_DISCONTINUITY",
                            f"{asset.asset_id} uses a different identity reference.",
                            f"asset:{asset.asset_id}",
                            ["regenerate_with_character_reference"],
                        )
                adjacent = bool(
                    previous_visual
                    and previous_visual.track == "a_roll"
                    and previous_visual.character_id == visual.character_id
                )
                if adjacent and previous_aroll_asset is not None and (
                    asset.previous_asset_id != previous_aroll_asset.asset_id
                    or asset.continuity_group_id
                    != previous_aroll_asset.continuity_group_id
                ):
                    _add(
                        issue_specs,
                        "AROLL_ACTION_DISCONTINUITY",
                        f"{asset.asset_id} is not anchored to the adjacent A-roll clip.",
                        f"asset:{asset.asset_id}",
                        ["regenerate_from_previous_last_frame"],
                    )
                if not adjacent and visual.character_id:
                    prior_group = last_group_by_character.get(visual.character_id)
                    if asset.previous_asset_id is not None or (
                        prior_group is not None
                        and asset.continuity_group_id == prior_group
                    ):
                        _add(
                            issue_specs,
                            "AROLL_FALSE_ACTION_CONTINUITY",
                            f"{asset.asset_id} must start a new action after B-roll.",
                            f"asset:{asset.asset_id}",
                            ["regenerate_from_identity_reference"],
                        )
                    if asset.continuity_group_id:
                        last_group_by_character[visual.character_id] = (
                            asset.continuity_group_id
                        )
                previous_aroll_asset = asset
            else:
                previous_aroll_asset = None
            previous_visual = visual
        prepared = {item.asset_id: item for item in prepared_images or []}
        inspections: list[AssetInspection] = []
        blocked_count = 0
        for asset in assets:
            path = root / asset.local_path
            asset_ref = f"asset:{asset.asset_id}"
            if not path.is_file() or path.stat().st_size == 0:
                _add(
                    issue_specs,
                    "ASSET_FILE_MISSING",
                    f"{asset.asset_id} local file is missing or empty.",
                    asset_ref,
                    ["retry_next_direction"],
                )
                continue
            if asset.modality == "ai_video" or not asset.mime_type.startswith("image/"):
                continue
            beat = beats.get(asset.beat_id)
            if beat is None:
                _add(
                    issue_specs,
                    "ASSET_BEAT_INVALID",
                    f"{asset.asset_id} references an unknown beat.",
                    asset_ref,
                    ["retry_next_direction"],
                )
                continue
            try:
                inspection = self.image_analyzer.analyze(
                    asset=asset,
                    beat=beat,
                    image_path=path,
                )
                if inspection.asset_id != asset.asset_id:
                    raise ValueError("inspection asset_id does not match the asset")
                with Image.open(path) as image:
                    width, height = image.size
            except Exception as exc:
                _add(
                    issue_specs,
                    "IMAGE_INSPECTION_FAILED",
                    f"{asset.asset_id} could not be inspected: {exc}",
                    asset_ref,
                    ["retry_next_direction"],
                )
                continue
            inspections.append(inspection)
            prepared_image = prepared.get(asset.asset_id)
            prepared_ok = False
            if prepared_image:
                prepared_path = root / prepared_image.output_path
                try:
                    with Image.open(prepared_path) as image:
                        prepared_ok = image.size == (1080, 1920)
                except (FileNotFoundError, OSError):
                    prepared_ok = False
            if inspection.blocking_overlay:
                blocked_count += 1
                _add(
                    issue_specs,
                    "LOGIN_OR_BLOCKING_OVERLAY",
                    f"{asset.asset_id} is obscured by a login, auth, or blocking overlay.",
                    asset_ref,
                    ["retry_next_direction"],
                )
                continue
            focal_area = inspection.focal_box[2] * inspection.focal_box[3]
            if focal_area < 0.12 and not prepared_ok:
                _add(
                    issue_specs,
                    "FOCUS_TARGET_TOO_SMALL",
                    f"{asset.asset_id} focal target occupies too little of the frame.",
                    f"prepared_image:{asset.asset_id}",
                    ["prepare_image"],
                )
            if (
                inspection.content_type in {"chart", "document", "webpage"}
                and inspection.text_readability == "poor"
                and not prepared_ok
            ):
                _add(
                    issue_specs,
                    "TEXT_UNREADABLE",
                    f"{asset.asset_id} key evidence text is not readable.",
                    f"prepared_image:{asset.asset_id}",
                    ["prepare_image"],
                )
            if (width < 1080 or height < 1920) and not prepared_ok:
                _add(
                    issue_specs,
                    "LOW_RESOLUTION",
                    f"{asset.asset_id} is below the 1080x1920 presentation target.",
                    f"prepared_image:{asset.asset_id}",
                    ["prepare_image"],
                )
            if not prepared_ok:
                _add(
                    issue_specs,
                    "PREPARED_IMAGE_MISSING",
                    f"{asset.asset_id} has no render-ready portrait image.",
                    f"prepared_image:{asset.asset_id}",
                    ["prepare_image"],
                )

        resolved = sum(item.status == "resolved" for item in resolutions)
        total = len(resolutions)
        coverage = resolved / total if total else 1.0
        messages = [item[1] for item in issue_specs]
        quality = AssetQuality(
            passed=coverage == 1 and violations == 0 and not issue_specs,
            visual_request_count=total,
            resolved_count=resolved,
            unresolved_count=total - resolved,
            resolution_coverage=round(coverage, 4),
            first_success_violations=violations,
            inspected_image_count=len(inspections),
            prepared_image_count=len(prepared),
            blocked_image_count=blocked_count,
            issues=messages,
        )
        critic_issues = [
            CriticIssue(
                issue_id=f"{self.critic_id}:{index:03d}",
                critic_id=self.critic_id,
                scope="asset",
                target_ref=issue_target,
                severity="error",
                code=code,
                diagnosis=message,
                repair_options=repair_options,
            )
            for index, (code, message, issue_target, repair_options) in enumerate(
                issue_specs, start=1
            )
        ]
        return quality, EvaluationResult(
            critic_id=self.critic_id,
            target_ref=target_ref,
            passed=quality.passed,
            issues=critic_issues,
        ), inspections


def _add(
    issues: list[tuple[str, str, str, list[str]]],
    code: str,
    diagnosis: str,
    target_ref: str,
    repair_options: list[str],
) -> None:
    issues.append((code, diagnosis, target_ref, repair_options))
