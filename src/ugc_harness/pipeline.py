from __future__ import annotations

import hashlib
import re
from typing import Protocol, TypeVar

from pydantic import BaseModel

from .models import (
    AudienceSpec,
    CommunicationSpec,
    CreativeBrief,
    PlanningArtifact,
    ScriptArtifact,
    StageOneArtifact,
    TargetSpec,
)
from .prompts import planning_prompt, script_prompt
from .prompts import planning_quality_repair_prompt, script_quality_repair_prompt
from .quality import estimate_duration_ms, evaluate

T = TypeVar("T", bound=BaseModel)


class JSONGenerator(Protocol):
    settings: object

    def generate(self, prompt: str, output_type: type[T]) -> T: ...


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
    )


class StageOnePipeline:
    def __init__(self, generator: JSONGenerator, model_name: str):
        self.generator = generator
        self.model_name = model_name

    def run(self, brief: CreativeBrief) -> StageOneArtifact:
        planning = self.generator.generate(
            planning_prompt(brief),
            PlanningArtifact,
        )
        plan_problems: list[str] = []
        close_section_id = planning.sections[-1].section_id
        if not any(
            beat.section_id == close_section_id
            and beat.discourse_role in {"payoff", "callback"}
            for beat in planning.beats
        ):
            plan_problems.append(
                "Close 中必须至少有一个 discourse_role 为 payoff 或 callback 的 Beat"
            )
        if plan_problems:
            planning = self.generator.generate(
                planning_quality_repair_prompt(
                    brief, planning, "；".join(plan_problems)
                ),
                PlanningArtifact,
            )

        script = self.generator.generate(
            script_prompt(brief, planning),
            ScriptArtifact,
        )
        estimated_ms = estimate_duration_ms(script)
        emphasis_missing = [
            f"{segment.script_segment_id}:{word}"
            for segment in script.segments
            for word in segment.delivery_hint.emphasis_words
            if word not in segment.text
        ]
        script_problems: list[str] = []
        if not 60_000 <= estimated_ms <= 120_000:
            script_problems.append(
                f"估算口播为 {estimated_ms / 1000:.1f}s，应落在 60–120s 的宽松窗口内"
            )
        if emphasis_missing:
            script_problems.append(
                f"这些重音词不在正文中：{emphasis_missing}"
            )
        if script_problems:
            script = self.generator.generate(
                script_quality_repair_prompt(
                    brief, planning, script, "；".join(script_problems)
                ),
                ScriptArtifact,
            )

        quality = evaluate(brief, planning, script)
        return StageOneArtifact(
            model=self.model_name,
            brief=brief,
            planning=planning,
            script=script,
            quality=quality,
        )
