"""Host-owned brief writeback. Not an MCP tool, not an agent action."""

from __future__ import annotations

import re
from typing import Protocol

from ..content import AudienceSpec, CommunicationSpec, TargetSpec
from .models import (
    AudiencePatch,
    BriefDraft,
    BriefPatch,
    CommunicationPatch,
    IntakeSession,
    TargetPatch,
    utc_now,
)


class BriefSync(Protocol):
    def apply(self, session: IntakeSession, user_text: str) -> IntakeSession: ...


def apply_brief_patch(current: BriefDraft, patch: BriefPatch) -> BriefDraft:
    updates: dict[str, object] = {}
    for name in ("project_id", "project_name", "topic", "production_mode", "video_profile"):
        value = getattr(patch, name)
        if isinstance(value, str):
            value = value.strip() or None
        if value is not None:
            updates[name] = value
    if patch.content_policy is not None:
        updates["content_policy"] = patch.content_policy
    if patch.target is not None:
        merged = _merge_target(current.target, patch.target)
        if merged is not None:
            updates["target"] = merged
    if patch.audience is not None:
        merged = _merge_audience(current.audience, patch.audience)
        if merged is not None:
            updates["audience"] = merged
    if patch.communication is not None:
        merged = _merge_communication(current.communication, patch.communication)
        if merged is not None:
            updates["communication"] = merged
    if not updates:
        return current
    return current.model_copy(update=updates)


def _merge_target(current: TargetSpec | None, patch: TargetPatch) -> TargetSpec | None:
    fields = {
        key: value
        for key, value in patch.model_dump().items()
        if value is not None
    }
    if not fields:
        return current
    return (current or TargetSpec()).model_copy(update=fields)


def _merge_audience(
    current: AudienceSpec | None, patch: AudiencePatch
) -> AudienceSpec | None:
    fields = {
        key: value
        for key, value in patch.model_dump().items()
        if value is not None
    }
    if not fields:
        return current
    return (current or AudienceSpec()).model_copy(update=fields)


def _merge_communication(
    current: CommunicationSpec | None, patch: CommunicationPatch
) -> CommunicationSpec | None:
    fields = {
        key: value
        for key, value in patch.model_dump().items()
        if value is not None
    }
    if not fields:
        return current
    if current is None:
        goal = fields.get("goal")
        if not isinstance(goal, str) or not goal.strip():
            return None
        return CommunicationSpec.model_validate(
            {"goal": goal, **{k: v for k, v in fields.items() if k != "goal"}}
        )
    return current.model_copy(update=fields)


class HeuristicBriefSync:
    """Deterministic extraction of fields the user explicitly said."""

    def apply(self, session: IntakeSession, user_text: str) -> IntakeSession:
        patch = extract_brief_patch(user_text)
        brief = apply_brief_patch(session.working_brief, patch)
        if brief is session.working_brief:
            return session
        return session.model_copy(
            update={"working_brief": brief, "updated_at": utc_now()}
        )


class ChainedBriefSync:
    def __init__(self, *syncs: BriefSync) -> None:
        self.syncs = syncs

    def apply(self, session: IntakeSession, user_text: str) -> IntakeSession:
        current = session
        for sync in self.syncs:
            current = sync.apply(current, user_text)
        return current


class LlmBriefSync:
    """Host-internal structured extract. Invisible to the agent as a tool."""

    def __init__(self, generator: object) -> None:
        self.generator = generator

    def apply(self, session: IntakeSession, user_text: str) -> IntakeSession:
        generate = getattr(self.generator, "generate", None)
        if not callable(generate):
            return session
        prompt = (
            "从用户这句话里抽出他明确说到的制作任务字段。"
            "没说的字段必须保持 null，不要编造，不要用默认值填满。"
            "时长写成毫秒，合法区间 60000–120000。"
            "讲解=explainer，短剧/剧情=drama，教程/步骤=tutorial。"
            f"\n当前 working_brief：{session.working_brief.model_dump_json()}\n"
            f"用户原话：{user_text}"
        )
        try:
            patch = generate(prompt, BriefPatch)
        except Exception:
            return session
        if not isinstance(patch, BriefPatch):
            return session
        brief = apply_brief_patch(session.working_brief, patch)
        if brief is session.working_brief:
            return session
        return session.model_copy(
            update={"working_brief": brief, "updated_at": utc_now()}
        )


_TOPIC_PATTERNS = (
    re.compile(r"主题是\s*[「\"“]?(.+?)[」\"”]?(?:[，。！？]|$)"),
    re.compile(r"做一条讲(.+?)的(?:抖音|快手|视频|短视频|短片)"),
    re.compile(r"做个讲(.+?)的(?:抖音|快手|视频|短视频|短片)"),
    re.compile(r"讲一下(.+?)(?:[，。！？]|$)"),
    re.compile(r"关于(.+?)(?:的(?:抖音|快手|视频|短视频|短片))?(?:[，。！？]|$)"),
)

_GOAL_PATTERNS = (
    re.compile(r"目标是(.+?)(?:[。！]|$)"),
    re.compile(r"希望观众(.+?)(?:[。！]|$)"),
    re.compile(r"看完(?:希望|能|要|可以)?(.+?)(?:[。！]|$)"),
)


def extract_brief_patch(text: str) -> BriefPatch:
    blob = text.strip()
    if not blob:
        return BriefPatch()
    topic = _first_match(_TOPIC_PATTERNS, blob)
    goal = _first_match(_GOAL_PATTERNS, blob)
    duration = _duration_ms(blob)
    platform = _platform(blob)
    mode = _production_mode(blob)
    profile = _video_profile(blob)
    target = None
    if duration is not None or platform is not None:
        target = TargetPatch(duration_target_ms=duration, platform=platform)
    communication = CommunicationPatch(goal=goal) if goal else None
    return BriefPatch(
        topic=topic,
        production_mode=mode,
        video_profile=profile,
        target=target,
        communication=communication,
    )


def _first_match(patterns: tuple[re.Pattern[str], ...], text: str) -> str | None:
    for pattern in patterns:
        matched = pattern.search(text)
        if matched:
            value = matched.group(1).strip(" ，。！？、")
            if 1 < len(value) <= 80:
                return value
    return None


def _duration_ms(text: str) -> int | None:
    if "一分半" in text or "1分半" in text:
        return 90_000
    if re.search(r"两分钟|2\s*分钟", text):
        return 120_000
    if re.search(r"一分钟|1\s*分钟", text):
        return 60_000
    matched = re.search(r"(\d+)\s*秒", text)
    if matched:
        seconds = int(matched.group(1))
        if 60 <= seconds <= 120:
            return seconds * 1000
    matched = re.search(r"(\d+)\s*分钟", text)
    if matched:
        minutes = int(matched.group(1))
        if minutes == 1:
            return 60_000
        if minutes == 2:
            return 120_000
    return None


def _platform(text: str) -> str | None:
    lowered = text.lower()
    if "抖音" in text or "douyin" in lowered:
        return "douyin"
    if "快手" in text:
        return "kuaishou"
    if "tiktok" in lowered:
        return "tiktok"
    if "youtube" in lowered:
        return "youtube"
    return None


def _production_mode(text: str) -> str | None:
    if any(token in text for token in ("短剧", "剧情", "drama")):
        return "drama"
    if any(token in text for token in ("教程", "步骤", "tutorial")):
        return "tutorial"
    if any(token in text for token in ("讲解", "explainer")):
        return "explainer"
    return None


def _video_profile(text: str) -> str | None:
    if "混剪" in text or "ab_roll" in text or "口播加画面" in text:
        return "ab_roll"
    if "口播" in text or "出镜" in text or "a_roll" in text:
        return "a_roll"
    if "画面" in text or "b_roll" in text:
        return "b_roll"
    return None
