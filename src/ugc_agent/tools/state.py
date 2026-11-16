from __future__ import annotations

from collections.abc import Callable

from langchain.tools import ToolRuntime, tool
from pydantic import BaseModel, Field

from ..context import AgentContext
from ..state import Deliverable, MediaAsset, Outline, ProjectState, ShotDesign, Story
from ..store import RevisionConflict

SAVE_ATTEMPTS = 16


class SaveOutlineInput(BaseModel):
    outline: Outline = Field(
        description="整层替换的大纲。只写主题、片种、受众、画面形态和投放硬指标，不要写长段剧情、口播或分镜。"
    )


class SaveStoryInput(BaseModel):
    story: Story = Field(
        description="整层替换的故事、口播或旁白。写法以当前 composition skill 为准。"
    )


class SaveShotDesignInput(BaseModel):
    shot_design: ShotDesign = Field(
        description="整层替换的分镜。生成素材和 record_media 之前必须先写入。人物、道具、场景、镜头一次给全。"
    )


class RecordMediaInput(BaseModel):
    asset: MediaAsset = Field(
        description="登记一条素材并挂到 owner_ref。生成工具只返回路径，必须再调这个工具写入账本。"
    )


class SetDeliverableInput(BaseModel):
    media_id: str = Field(description="已在 media 里登记过的成片 media_id。")


def _save(
    runtime: ToolRuntime[AgentContext],
    mutate: Callable[[ProjectState], ProjectState],
) -> dict:
    store = runtime.context.project_store
    project_id = runtime.context.project_id
    last_error: RevisionConflict | None = None
    for _ in range(SAVE_ATTEMPTS):
        try:
            return store.save(mutate(store.load(project_id))).model_dump(mode="json")
        except RevisionConflict as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


@tool
def read_project(runtime: ToolRuntime[AgentContext]) -> dict:
    """Read the current project state."""
    return runtime.context.project_store.load(runtime.context.project_id).model_dump(
        mode="json"
    )


@tool(args_schema=SaveOutlineInput)
def save_outline(
    outline: Outline,
    runtime: ToolRuntime[AgentContext],
) -> dict:
    """Replace the project outline."""
    return _save(runtime, lambda state: state.model_copy(update={"outline": outline}))


@tool(args_schema=SaveStoryInput)
def save_story(
    story: Story,
    runtime: ToolRuntime[AgentContext],
) -> dict:
    """Replace the project story."""
    return _save(runtime, lambda state: state.model_copy(update={"story": story}))


@tool(args_schema=SaveShotDesignInput)
def save_shot_design(
    shot_design: ShotDesign,
    runtime: ToolRuntime[AgentContext],
) -> dict:
    """Replace the project shot design."""
    return _save(
        runtime, lambda state: state.model_copy(update={"shot_design": shot_design})
    )


@tool(args_schema=RecordMediaInput)
def record_media(
    asset: MediaAsset,
    runtime: ToolRuntime[AgentContext],
) -> dict:
    """Register a media asset and attach it to its owner_ref."""

    def mutate(state: ProjectState) -> ProjectState:
        updated = state.model_copy(deep=True)
        updated.media[asset.media_id] = asset
        _attach_media(updated, asset)
        return updated

    return _save(runtime, mutate)


@tool(args_schema=SetDeliverableInput)
def set_deliverable(
    media_id: str,
    runtime: ToolRuntime[AgentContext],
) -> dict:
    """Set a registered media asset as the final deliverable."""

    def mutate(state: ProjectState) -> ProjectState:
        if media_id not in state.media:
            raise ValueError(f"unknown media_id: {media_id}")
        return state.model_copy(update={"deliverable": Deliverable(media_id=media_id)})

    return _save(runtime, mutate)


def _attach_media(state: ProjectState, asset: MediaAsset) -> None:
    if asset.owner_ref == "project":
        return
    if state.shot_design is None:
        raise ValueError("shot design is missing")

    kind, owner_id = asset.owner_ref.split(":", 1)
    groups = {
        "character": state.shot_design.characters,
        "prop": state.shot_design.props,
        "place": state.shot_design.places,
        "shot": state.shot_design.shots,
    }
    owner = next(
        (item for item in groups[kind] if getattr(item, f"{kind}_id", None) == owner_id),
        None,
    )
    if owner is None and kind in {"character", "prop", "place"}:
        owner = next(
            (item for item in groups[kind] if item.entity_id == owner_id),
            None,
        )
    if owner is None:
        raise ValueError(f"unknown owner_ref: {asset.owner_ref}")
    if asset.media_id not in owner.media_ids:
        owner.media_ids.append(asset.media_id)


PROJECT_TOOLS = [
    read_project,
    save_outline,
    save_story,
    save_shot_design,
    record_media,
    set_deliverable,
]
