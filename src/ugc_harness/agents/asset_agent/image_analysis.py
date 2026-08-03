from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import httpx
from PIL import Image

from ...shared.settings import LLMSettings
from ..voice_agent.models import RealizedBeat
from .models import AssetCard
from .image_models import AssetInspection


class BasicImageAnalyzer:
    """Local deterministic fallback used when no vision model is configured."""

    def analyze(
        self,
        *,
        asset: AssetCard,
        beat: RealizedBeat,
        image_path: Path,
    ) -> AssetInspection:
        with Image.open(image_path) as image:
            image.verify()
        is_document = asset.modality in {
            "source_screenshot",
            "document_screenshot",
            "chart",
        }
        return AssetInspection(
            asset_id=asset.asset_id,
            analyzer_model="local-image-check",
            content_type="document" if is_document else "photo",
            focal_box=(0.0, 0.0, 1.0, 1.0),
            focus_confidence=0.5,
            preserve_full_frame=is_document,
            blocking_overlay=False,
            text_readability="acceptable" if is_document else "none",
            recommended_strategy=(
                "contained_background" if is_document else "subject_cover"
            ),
            reason="Local image integrity and presentation check.",
        )


class OpenRouterImageAnalyzer:
    def __init__(self, settings: LLMSettings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=settings.timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "ugc-video-harness/0.7"},
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "OpenRouterImageAnalyzer":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def analyze(
        self,
        *,
        asset: AssetCard,
        beat: RealizedBeat,
        image_path: Path,
    ) -> AssetInspection:
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        response = self.client.post(
            self.settings.base_url.rstrip("/") + "/chat/completions",
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.settings.model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "你是竖屏 UGC 图片预处理分析器。结合旁白定位真正需要"
                            "保留和放大的主体、图表或文档区域。坐标全部使用 0 到 1"
                            "的归一化 x,y,width,height。只返回 JSON。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": f"""旁白：{beat.narration}
素材类型：{asset.modality}
素材来源：{asset.origin}

请判断最重要的画面区域。图表、文档和网页应优先让与旁白相关的数据、标题或
段落可读；照片应保留人物或核心物体。再次检查是否有登录、验证码、认证或订阅
遮罩覆盖主体。

只返回：
{{
  "content_type":"photo|illustration|chart|document|webpage|meme|other",
  "focal_box":[x,y,width,height],
  "focus_confidence":0.0,
  "preserve_full_frame":false,
  "blocking_overlay":false,
  "text_readability":"none|poor|acceptable|good",
  "key_text":["最多三条与旁白有关的可见文字"],
  "recommended_strategy":"portrait_normalize|subject_cover|focus_crop|contained_background",
  "reason":"简短原因"
}}""",
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": (
                                        f"data:{asset.mime_type};base64,{encoded}"
                                    )
                                },
                            },
                        ],
                    },
                ],
            },
        )
        response.raise_for_status()
        payload = response.json()
        text = payload["choices"][0]["message"].get("content") or ""
        value = _parse_json_object(text)
        return AssetInspection(
            asset_id=asset.asset_id,
            analyzer_model=self.settings.model,
            content_type=value.get("content_type"),
            focal_box=tuple(value.get("focal_box") or (0, 0, 1, 1)),
            focus_confidence=value.get("focus_confidence"),
            preserve_full_frame=value.get("preserve_full_frame"),
            blocking_overlay=value.get("blocking_overlay"),
            text_readability=value.get("text_readability"),
            key_text=value.get("key_text") or [],
            recommended_strategy=value.get("recommended_strategy"),
            reason=str(value.get("reason") or "").strip(),
        )


def _parse_json_object(text: str) -> dict:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("image analyzer did not return JSON")
    value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("image analyzer response is not an object")
    return value
