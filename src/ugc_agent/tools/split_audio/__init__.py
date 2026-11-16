from __future__ import annotations

from langchain.tools import ToolRuntime, tool
from pydantic import BaseModel, Field

from ...context import AgentContext
from .client import split_audio_file

__all__ = ["split_audio"]


class SplitAudioInput(BaseModel):
    video_path: str = Field(description="要从中拆出音频的视频本地路径，通常是上一段已生成的成片。")


@tool("split-audio", args_schema=SplitAudioInput)
def split_audio(
    video_path: str,
    runtime: ToolRuntime[AgentContext],
) -> dict:
    """从一段视频里拆出完整音轨，保存为 mp3。

使用时机：
- 演绎短剧：生成第 n+1 段前，对第 n 段成片调用，把返回路径作为下一段 video-gen 的 audio_paths
- 只要上一段的音色和 BGM 参考，不要拆成多段口播

不要做：
- 数字人、广告、科普不要用这个工具驱动下一段口播
- 不要把结果写入项目状态；拿到 path 后自己 record_media

返回：
- kind：audio
- path：mp3 路径
"""
    directory = (
        runtime.context.project_store.root
        / runtime.context.project_id
        / "media"
        / "audio"
    )
    path = split_audio_file(video_path, directory)
    return {"kind": "audio", "path": str(path)}
