from __future__ import annotations

import hashlib
import re

from .models import AudienceSpec, CommunicationSpec, CreativeBrief, TargetSpec
from ...profiles.models import VideoProfileRequest


def _project_id(topic: str) -> str:
    ascii_part = re.sub(r"[^a-z0-9]+", "_", topic.lower()).strip("_")[:30]
    digest = hashlib.sha1(topic.encode("utf-8")).hexdigest()[:8]
    return f"ugc_{ascii_part or 'topic'}_{digest}"


def make_brief(
    *,
    topic: str,
    project_name: str | None = None,
    duration_seconds: int = 90,
    platform: str = "douyin",
    audience: str = "对主题感兴趣、但没有专业背景的普通用户",
    goal: str | None = None,
    tone: list[str] | None = None,
    creator_persona: str = "像朋友一样解释复杂话题的知识型创作者",
    video_profile: VideoProfileRequest = "auto",
) -> CreativeBrief:
    clean_topic = topic.strip()
    if not clean_topic:
        raise ValueError("topic cannot be empty")
    return CreativeBrief(
        project_id=_project_id(clean_topic),
        project_name=(project_name or clean_topic).strip(),
        topic=clean_topic,
        target=TargetSpec(
            platform=platform,
            duration_target_ms=duration_seconds * 1000,
        ),
        audience=AudienceSpec(description=audience),
        communication=CommunicationSpec(
            goal=goal or f"让目标观众理解“{clean_topic}”最重要的结论及其影响",
            tone=tone
            or ["conversational", "information_dense", "slightly_surprising"],
            creator_persona=creator_persona,
        ),
        video_profile=video_profile,
    )
