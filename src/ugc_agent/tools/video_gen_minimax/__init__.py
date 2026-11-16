from __future__ import annotations

from typing import Literal

from langchain.tools import ToolRuntime, tool
from pydantic import BaseModel, Field

from ...context import AgentContext
from .client import generate_video, write_video

__all__ = ["video_gen_minimax"]


class VideoGenMinimaxInput(BaseModel):
    prompt: str = Field(
        description=(
            "这一段视频的提示词。按 h3-prompt：主体、场景、动作、运镜、分时段、音频、风格。"
            "参考图按实际传入顺序写成图1 图2，参考视频写成视频1，ref2va 的参考音频写成音频1。"
            "每条引用必须写明用途。单次图片≤9、视频≤3、音频≤3。"
        )
    )
    duration_seconds: float = Field(
        gt=0,
        description="这一段时长，秒。MiniMax H3 有效范围大约 4 到 15 秒，超出时按两端截取。",
    )
    mode: Literal["fl2va", "ref2va"] = Field(
        description=(
            "生成模式，必须显式选择。"
            "fl2va：首帧生视频，模型自己出声，reference_paths 只能传一张首帧图，禁止 audio_paths、禁止参考视频和其他参考图。"
            "ref2va：多参考生视频，图片当 reference_image，视频当 reference_video，口播或上一段音频走 audio_paths。"
        )
    )
    reference_paths: list[str] | None = Field(
        default=None,
        description=(
            "参考素材路径。"
            "fl2va：只传一张首帧图。"
            "ref2va：可传多张参考图（≤9）。连续镜头必须把上一镜尾帧也放进 reference_paths，prompt 写成作为首帧；连续镜头才加一段上一段视频（视频合计≤3）；回锚不要带上一段视频和上一镜尾帧。"
        ),
    )
    audio_paths: list[str] | None = Field(
        default=None,
        description=(
            "参考音频路径，仅 ref2va 使用，合计≤3。"
            "数字人/广告/科普传当前段口播；演绎传上一段 split-audio。"
            "fl2va 不要传这个参数。"
        ),
    )
    aspect_ratio: str = Field(
        default="9:16",
        description="画面比例，默认 9:16。可选 16:9、1:1、adaptive 等。",
    )


@tool("video-gen-minimax", args_schema=VideoGenMinimaxInput)
def video_gen_minimax(
    prompt: str,
    duration_seconds: float,
    mode: Literal["fl2va", "ref2va"],
    runtime: ToolRuntime[AgentContext],
    reference_paths: list[str] | None = None,
    audio_paths: list[str] | None = None,
    aspect_ratio: str = "9:16",
) -> dict:
    """用 MiniMax H3 生成一段视频并保存到当前项目。必须先选 mode。提示词按 h3-prompt 写。

fl2va：
- 首帧生视频，画面从传入的那一张图开始动
- 没有参考音频，不要传 audio_paths，声音由模型自己生成
- reference_paths 只能有一张首帧图，不能带参考视频或其他参考图

ref2va：
- 多参考生视频：人物/产品/四宫格等走参考图；连续镜头必须把上一镜尾帧也放进 reference_paths，prompt 写成作为首帧；连续镜头才带上一段视频
- 数字人/广告/科普把当前段口播放进 audio_paths
- 演绎把上一段 split-audio 放进 audio_paths
- 回锚镜头只回原始锚图，不带上一段视频

不要做：
- 不要把 fl2va 和参考音频混用
- 不要把全部历史视频或音频传给当前段
- 不要把结果写入项目状态；拿到 path 后自己 record_media

返回：
- kind：video
- path：保存后的 mp4 路径
"""
    stripped = prompt.strip()
    if not stripped:
        raise ValueError("prompt is empty")
    data = generate_video(
        stripped,
        duration_seconds=duration_seconds,
        mode=mode,
        reference_paths=reference_paths,
        audio_paths=audio_paths,
        aspect_ratio=aspect_ratio,
    )
    directory = (
        runtime.context.project_store.root
        / runtime.context.project_id
        / "media"
        / "videos"
    )
    path = write_video(directory, data)
    return {"kind": "video", "path": str(path)}
