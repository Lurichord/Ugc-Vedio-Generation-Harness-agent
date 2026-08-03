from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ...harness.models import (
    ActionRecord,
    AgentResult,
    ArtifactRef,
    StatePatch,
    TaskEnvelope,
)
from ..base import BaseAgent, failed_result
from ..editorial_agent.models import EditorialArtifact
from ..voice_agent.models import VoiceArtifact
from .image_models import AssetInspection, PreparedImage
from .models import AssetArtifact, AssetCard, VisualResolution


class AssetCandidate(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    assets: list[AssetCard]
    resolutions: list[VisualResolution]
    inspections: list[AssetInspection] = Field(default_factory=list)
    prepared_images: list[PreparedImage] = Field(default_factory=list)


class AssetAgentExecution(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    candidate: AssetCandidate | None = None
    result: AgentResult


class AssetAgent(BaseAgent[AssetAgentExecution]):
    name = "asset_agent"
    ACQUIRE_TOOL = "asset.acquire_requirement"
    PREPARE_TOOL = "asset.prepare_image"

    def run(self, task: TaskEnvelope, **kwargs: object) -> AssetAgentExecution:
        self.validate_task(task)
        editorial = kwargs.get("editorial")
        voice = kwargs.get("voice")
        project_dir = kwargs.get("project_dir")
        current = kwargs.get("current_artifact")
        if not isinstance(editorial, EditorialArtifact):
            raise TypeError("AssetAgent requires an EditorialArtifact")
        if not isinstance(voice, VoiceArtifact):
            raise TypeError("AssetAgent requires a VoiceArtifact")
        if not isinstance(project_dir, (str, Path)):
            raise TypeError("AssetAgent requires a project directory")
        if current is not None and not isinstance(current, AssetArtifact):
            raise TypeError("current_artifact must be an AssetArtifact")

        if (
            task.allowed_tools == [AssetAgent.PREPARE_TOOL]
            or (
                task.scope.target_refs
                and all(
                    ref.startswith("prepared_image:")
                    for ref in task.scope.target_refs
                )
            )
        ):
            return self._prepare_images(task, current, project_dir)

        visuals = editorial.editorial_plan.visual_requirements
        requested = set(task.scope.visual_request_ids)
        if task.scope.target_refs and not requested:
            raise ValueError("asset repair task has no visual_request_ids")
        target_visuals = [
            visual for visual in visuals if not requested or visual.visual_request_id in requested
        ]
        if requested - {item.visual_request_id for item in target_visuals}:
            raise ValueError("asset task references an unknown visual requirement")
        if task.scope.target_refs and current is None:
            raise ValueError("asset repair requires the current AssetArtifact")

        beats = {beat.beat_id: beat for beat in voice.realized_beats}
        assets_by_request = {
            item.visual_request_id: item for item in (current.assets if current else [])
        }
        resolutions_by_request = {
            item.visual_request_id: item
            for item in (current.resolutions if current else [])
        }
        inspections_by_asset = {
            item.asset_id: item for item in (current.inspections if current else [])
        }
        prepared_by_asset = {
            item.asset_id: item
            for item in (current.prepared_images if current else [])
        }
        group_by_request: dict[str, str] = {}
        group_index = 0
        previous_visual = None
        for visual in visuals:
            if visual.track == "a_roll":
                if not (
                    previous_visual
                    and previous_visual.track == "a_roll"
                    and previous_visual.character_id == visual.character_id
                ):
                    group_index += 1
                group_by_request[visual.visual_request_id] = (
                    f"{visual.character_id}_group_{group_index:02d}"
                )
            previous_visual = visual

        actions: list[ActionRecord] = []
        try:
            for visual in target_visuals:
                beat = beats.get(visual.beat_id)
                if beat is None:
                    raise ValueError(
                        f"VisualRequirement references unknown beat: {visual.beat_id}"
                    )
                visual_index = visuals.index(visual)
                prior_visual = visuals[visual_index - 1] if visual_index else None
                adjacent_aroll = bool(
                    prior_visual
                    and prior_visual.track == "a_roll"
                    and visual.track == "a_roll"
                    and prior_visual.character_id == visual.character_id
                )
                previous_character_asset = (
                    assets_by_request.get(prior_visual.visual_request_id)
                    if adjacent_aroll and prior_visual
                    else None
                )
                acquired = self.invoke_tool(
                    task,
                    actions,
                    self.ACQUIRE_TOOL,
                    project_id=editorial.project_id,
                    visual=visual,
                    beat=beat,
                    project_dir=project_dir,
                    character_description=(
                        editorial.editorial_plan.video_profile.character_description
                        if visual.track == "a_roll"
                        else None
                    ),
                    continuity_group_id=group_by_request.get(
                        visual.visual_request_id
                    ),
                    previous_character_asset=previous_character_asset,
                )
                if not (
                    isinstance(acquired, tuple)
                    and len(acquired) == 2
                    and (acquired[0] is None or isinstance(acquired[0], AssetCard))
                    and isinstance(acquired[1], VisualResolution)
                ):
                    raise TypeError("asset tool returned an invalid acquisition result")
                asset, resolution = acquired
                assets_by_request.pop(visual.visual_request_id, None)
                if current_asset := next(
                    (
                        item
                        for item in (current.assets if current else [])
                        if item.visual_request_id == visual.visual_request_id
                    ),
                    None,
                ):
                    inspections_by_asset.pop(current_asset.asset_id, None)
                    prepared_by_asset.pop(current_asset.asset_id, None)
                if asset is not None:
                    assets_by_request[visual.visual_request_id] = asset
                resolutions_by_request[visual.visual_request_id] = resolution
        except Exception as exc:
            return AssetAgentExecution(result=failed_result(task, actions, exc))

        ordered_ids = [item.visual_request_id for item in visuals]
        candidate = AssetCandidate(
            assets=[assets_by_request[ref] for ref in ordered_ids if ref in assets_by_request],
            resolutions=[
                resolutions_by_request[ref]
                for ref in ordered_ids
                if ref in resolutions_by_request
            ],
            inspections=list(inspections_by_asset.values()),
            prepared_images=list(prepared_by_asset.values()),
        )
        return AssetAgentExecution(
            candidate=candidate,
            result=AgentResult(
                task_id=task.task_id,
                status="completed",
                state_version_used=task.based_on_state_version,
                input_hash=task.input_hash,
                actions=actions,
                artifact_refs=[
                    ArtifactRef(kind="asset_candidate", id=editorial.project_id)
                ],
                state_patch=StatePatch(
                    set={"video.asset_status": "ready"},
                    invalidate=["timeline:all", "render:final"],
                ),
                evaluation_target=(
                    f"assets:{editorial.project_id}@"
                    f"{task.based_on_state_version + 1}"
                ),
            ),
        )

    def _prepare_images(
        self,
        task: TaskEnvelope,
        current: AssetArtifact | None,
        project_dir: str | Path,
    ) -> AssetAgentExecution:
        assert current is not None
        assets = {item.asset_id: item for item in current.assets}
        inspections = {item.asset_id: item for item in current.inspections}
        prepared = {item.asset_id: item for item in current.prepared_images}
        requested = set(task.scope.asset_ids)
        if not requested:
            raise ValueError("image repair task has no asset_ids")
        actions: list[ActionRecord] = []
        try:
            for asset_id in sorted(requested):
                asset = assets.get(asset_id)
                inspection = inspections.get(asset_id)
                if asset is None or inspection is None:
                    raise ValueError(
                        f"image repair is missing asset or inspection: {asset_id}"
                    )
                result = self.invoke_tool(
                    task,
                    actions,
                    self.PREPARE_TOOL,
                    asset=asset,
                    inspection=inspection,
                    project_dir=project_dir,
                )
                if not isinstance(result, PreparedImage):
                    raise TypeError("asset.prepare_image returned an invalid result")
                prepared[asset_id] = result
        except Exception as exc:
            return AssetAgentExecution(result=failed_result(task, actions, exc))
        return AssetAgentExecution(
            candidate=AssetCandidate(
                assets=current.assets,
                resolutions=current.resolutions,
                inspections=current.inspections,
                prepared_images=list(prepared.values()),
            ),
            result=AgentResult(
                task_id=task.task_id,
                status="completed",
                state_version_used=task.based_on_state_version,
                input_hash=task.input_hash,
                actions=actions,
                artifact_refs=[ArtifactRef(kind="prepared_images", id=current.project_id)],
                state_patch=StatePatch(
                    set={"video.asset_status": "ready"},
                    invalidate=["timeline:all", "render:final"],
                ),
                evaluation_target=(
                    f"assets:{current.project_id}@{task.based_on_state_version + 1}"
                ),
            ),
        )
