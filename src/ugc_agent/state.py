from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Target(StateModel):
    platform: str = Field(default="douyin", description="投放平台，例如 douyin。")
    duration_seconds: int = Field(default=60, gt=0, description="成片目标时长，秒。")
    aspect_ratio: str = Field(default="9:16", description="画面比例，默认 9:16。")
    language: str = Field(default="zh-CN", description="口播或对白语言。")


class Outline(StateModel):
    topic: str = Field(description="片子主题。")
    video_type: Literal["explainer", "avatar-explainer", "drama", "ad"] = Field(
        description="片种。explainer 科普，avatar-explainer 数字人讲解，drama 演绎短剧，ad 广告。"
    )
    audience: str = Field(description="目标受众。")
    target: Target = Field(
        default_factory=Target,
        description="投放硬指标：平台、时长、画幅、语言。",
    )
    visual_form: str = Field(
        description="画面形态和制作约定。分辨率、字幕、配音、BGM、数字人出镜等写这里，不要写长段剧情或分镜。"
    )


class Story(StateModel):
    text: str = Field(
        description="完整故事、口播或旁白正文。具体写法以当前 composition skill 为准。"
    )


class DesignEntity(StateModel):
    entity_id: str = Field(
        description="实体 ID，例如 c1、p1、loc1。挂媒体时 owner_ref 用这个 ID。"
    )
    description: str = Field(description="外形或外观描述，供后续生成参考。")
    media_ids: list[str] = Field(
        default_factory=list,
        description="已挂到该实体的 media_id。写入分镜时通常留空，由 record_media 追加。",
    )


class Shot(StateModel):
    shot_id: str = Field(
        description="镜头 ID，例如 shot_01。挂媒体时 owner_ref 为 shot:shot_01。"
    )
    duration_seconds: float = Field(
        gt=0, description="这一镜时长，秒。不要超过 video-gen 上限。"
    )
    prompt: str = Field(
        description=(
            "这一镜交给 video-gen 的提示词。按 seedance-prompt 写主体、场景、动作、运镜、分时段、音频、风格。"
            "参考按实际传入顺序写成[图1][视频1][音频1]。镜头类型可写在开头一行。"
            "不要把未生成的文件名写进 prompt。"
        )
    )
    character_ids: list[str] = Field(
        default_factory=list, description="本镜出场人物的 entity_id。"
    )
    prop_ids: list[str] = Field(
        default_factory=list, description="本镜道具的 entity_id。"
    )
    place_ids: list[str] = Field(
        default_factory=list, description="本镜场景的 entity_id。"
    )
    media_ids: list[str] = Field(
        default_factory=list,
        description="已挂到该镜头的 media_id。写入分镜时通常留空，由 record_media 追加。",
    )


class ShotDesign(StateModel):
    style: str = Field(description="全片视觉风格。")
    characters: list[DesignEntity] = Field(
        default_factory=list, description="人物。数字人、出镜角色挂这里。"
    )
    props: list[DesignEntity] = Field(
        default_factory=list, description="道具。产品、图示挂这里。"
    )
    places: list[DesignEntity] = Field(
        default_factory=list, description="场景。演播背景、环境挂这里。"
    )
    shots: list[Shot] = Field(
        default_factory=list,
        description="按播放顺序的镜头执行表。生成和挂账前必须先写入这一层。",
    )


class MediaAsset(StateModel):
    media_id: str = Field(description="素材 ID，项目内唯一。")
    kind: str = Field(description="素材类型，例如 image、audio、video、timestamp。")
    path: str = Field(description="本地文件路径。生成工具返回的 path 原样传入。")
    owner_ref: str = Field(
        description=(
            "挂载目标。character:c1 / prop:p1 / place:loc1 / shot:shot_01 / project。"
            "挂到人物、道具、场景或镜头前，必须已有 shot_design，且对应实体已存在。"
        )
    )
    source: Literal["input", "generated"] = Field(
        description="input 表示用户提供，generated 表示工具生成。"
    )
    mime_type: str | None = Field(
        default=None,
        description="可选 MIME，例如 image/png、audio/mpeg、video/mp4。",
    )


class Deliverable(StateModel):
    media_id: str


class ProjectState(StateModel):
    schema_version: str = "project-state.v1"
    project_id: str
    revision: int = Field(default=0, ge=0)
    outline: Outline | None = None
    story: Story | None = None
    shot_design: ShotDesign | None = None
    media: dict[str, MediaAsset] = Field(default_factory=dict)
    deliverable: Deliverable | None = None
