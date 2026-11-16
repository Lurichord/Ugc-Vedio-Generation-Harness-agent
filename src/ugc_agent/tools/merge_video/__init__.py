from __future__ import annotations

from langchain.tools import ToolRuntime, tool
from pydantic import BaseModel, Field

from ...context import AgentContext
from .client import merge_videos

__all__ = ["merge_video"]


class MergeVideoInput(BaseModel):
    video_paths: list[str] = Field(
        description="要拼接的分段视频路径，必须按镜头顺序，不要打乱。"
    )


@tool("merge-video", args_schema=MergeVideoInput)
def merge_video(
    video_paths: list[str],
    runtime: ToolRuntime[AgentContext],
) -> dict:
    """按顺序把多段视频拼成一条成片。

使用时机：
- 全部需要生成的镜头都已经有视频之后
- 按镜头顺序传入路径，不要漏段、不要重排

不要做：
- 不要在还缺镜头时调用
- 不要把结果写入项目状态；拿到 path 后自己 record_media，再用 set_deliverable 登记成片

返回：
- kind：video
- path：拼接后的 mp4 路径
"""
    if not video_paths:
        raise ValueError("video_paths is empty")
    directory = (
        runtime.context.project_store.root
        / runtime.context.project_id
        / "media"
        / "videos"
    )
    path = merge_videos(video_paths, directory)
    return {"kind": "video", "path": str(path)}
