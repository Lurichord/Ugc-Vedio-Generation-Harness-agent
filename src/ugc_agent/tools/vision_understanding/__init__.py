from __future__ import annotations

from langchain.tools import tool
from pydantic import BaseModel, Field

from .client import describe_media

__all__ = ["vision_understanding"]


class VisionUnderstandingInput(BaseModel):
    prompt: str = Field(
        description="要看什么、检查什么。例如提取人物服装姿态，或判断这段生成是否符合分镜。"
    )
    media_paths: list[str] | None = Field(
        default=None,
        description="要理解的图片或视频本地路径。参考素材、生成结果、尾帧都可以。",
    )


@tool("vision-understanding", args_schema=VisionUnderstandingInput)
def vision_understanding(
    prompt: str,
    media_paths: list[str] | None = None,
) -> str:
    """看图片或视频并返回文字理解结果。使用 UGC_LLM_MODEL。

使用时机：
- 整理输入时理解参考图、参考视频
- 生成后检查人物、产品、讲解内容、卖点是否合格
- 不合格就根据返回内容重做当前段，不要跳过

不要做：
- 这不是生图或生视频工具
- 不要把这段文字当成已经生成的素材去 record_media

返回：一段中文描述或检查结论。
"""
    stripped = prompt.strip()
    if not stripped:
        raise ValueError("prompt is empty")
    return describe_media(stripped, media_paths)
