from __future__ import annotations

from langchain.tools import ToolRuntime, tool
from pydantic import BaseModel, Field

from ...context import AgentContext
from .client import extract_frames

__all__ = ["extract_video_frame"]


class ExtractVideoFrameInput(BaseModel):
    video_path: str = Field(description="要抽帧的视频本地路径。")
    count: int = Field(
        default=1,
        ge=1,
        le=10,
        description="抽取张数。抽尾帧用 1；数字人从参考视频抽人物帧用 3 到 5。",
    )
    at: str | None = Field(
        default=None,
        description="抽帧位置。end=尾帧，start=开头，even=均匀取样，或直接给秒数如 1.2。count=1 时默认 end，多张时默认 even。",
    )


@tool("extract-video-frame", args_schema=ExtractVideoFrameInput)
def extract_video_frame(
    video_path: str,
    runtime: ToolRuntime[AgentContext],
    count: int = 1,
    at: str | None = None,
) -> dict:
    """从视频里抽出一帧或多帧图片。

使用时机：
- 数字人：从参考视频抽 3 到 5 张人物帧（count=3 到 5）
- 各片种逐段视频生成后抽尾帧（count=1，默认 end）
- 科普也可从参考视频抽关键帧

不要做：
- 不要把结果写入项目状态；拿到路径后自己 record_media
- 不要用这个工具合成口播或生成新视频

返回：
- kind：image
- path：第一张（或仅有一张）png 路径
- paths：全部 png 路径
"""
    directory = (
        runtime.context.project_store.root
        / runtime.context.project_id
        / "media"
        / "images"
    )
    paths = extract_frames(video_path, directory, count=count, at=at)
    rendered = [str(path) for path in paths]
    return {"kind": "image", "path": rendered[0], "paths": rendered}
