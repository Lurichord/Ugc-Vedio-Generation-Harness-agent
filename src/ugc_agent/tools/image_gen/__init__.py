from __future__ import annotations

from langchain.tools import ToolRuntime, tool
from pydantic import BaseModel, Field

from ...context import AgentContext
from .client import generate_image, write_image

__all__ = ["image_gen"]


class ImageGenInput(BaseModel):
    prompt: str = Field(
        description="图像提示词。参考图按实际传入顺序写成[图1][图2]，不要引用未传入的图。"
    )
    reference_paths: list[str] | None = Field(
        default=None,
        description="可选参考图本地路径。人物锚图、三视图、产品图、四宫格都用这个参数传入已有图。",
    )
    size: str = Field(
        default="2K",
        description="输出分辨率档位，Seedream 5.0 常用 1K / 1.5K / 2K。默认 2K。",
    )


@tool("image-gen", args_schema=ImageGenInput)
def image_gen(
    prompt: str,
    runtime: ToolRuntime[AgentContext],
    reference_paths: list[str] | None = None,
    size: str = "2K",
) -> dict:
    """生成一张图片并保存到当前项目。

使用时机：
- 数字人：人物主锚图、三视图、演播背景、产品锚图
- 广告：产品三视图、风格参考
- 科普：场景、道具、四宫格
- 演绎：角色三视图、场景、道具
- 需要参考已有图时传入 reference_paths

不要做：
- 不要把结果写入项目状态；拿到 path 后自己 record_media
- 不要一次生成整段视频

返回：
- kind：image
- path：保存后的 png 路径
"""
    stripped = prompt.strip()
    if not stripped:
        raise ValueError("prompt is empty")
    data = generate_image(
        stripped,
        reference_paths=reference_paths,
        size=size,
    )
    directory = (
        runtime.context.project_store.root
        / runtime.context.project_id
        / "media"
        / "images"
    )
    path = write_image(directory, data)
    return {"kind": "image", "path": str(path)}
