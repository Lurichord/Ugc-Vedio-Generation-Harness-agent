from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from ...harness.models import (
    ActionRecord,
    AgentResult,
    ArtifactRef,
    StatePatch,
    TaskEnvelope,
)
from .models import CreativeBrief, PlanningArtifact, ScriptArtifact
from .prompts import (
    planning_prompt,
    planning_quality_repair_prompt,
    script_prompt,
    script_quality_repair_prompt,
)
from .quality import estimate_duration_ms
from ..base import BaseAgent, failed_result


class NarrativeAgentExecution(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    planning: PlanningArtifact | None = None
    script: ScriptArtifact | None = None
    result: AgentResult


class NarrativeAgent(BaseAgent[NarrativeAgentExecution]):
    """Plans and writes narrative through an allow-listed, budgeted tool loop."""

    name = "narrative_agent"
    PLAN_TOOL = "narrative.generate_plan"
    SCRIPT_TOOL = "narrative.generate_script"

    def run(
        self,
        task: TaskEnvelope,
        **kwargs: object,
    ) -> NarrativeAgentExecution:
        self.validate_task(task)
        brief = kwargs.get("brief")
        if not isinstance(brief, CreativeBrief):
            raise TypeError("NarrativeAgent requires a CreativeBrief")
        actions: list[ActionRecord] = []
        try:
            planning = self._generate_plan(task, brief, actions)
            script = self._generate_script(task, brief, planning, actions)
        except Exception as exc:
            return NarrativeAgentExecution(
                result=failed_result(task, actions, exc)
            )

        result = AgentResult(
            task_id=task.task_id,
            status="completed",
            state_version_used=task.based_on_state_version,
            input_hash=task.input_hash,
            actions=actions,
            artifact_refs=[
                ArtifactRef(kind="narrative_plan", id=brief.project_id),
                ArtifactRef(kind="script", id=brief.project_id),
            ],
            state_patch=StatePatch(
                set={
                    "video.narrative_status": "ready",
                    "video.script_status": "ready",
                },
                invalidate=[
                    "voice:all",
                    "editorial:all",
                    "timeline:all",
                    "render:final",
                ],
            ),
            evaluation_target=(
                f"narrative:{brief.project_id}@{task.based_on_state_version + 1}"
            ),
        )
        return NarrativeAgentExecution(
            planning=planning,
            script=script,
            result=result,
        )

    def _generate_plan(
        self,
        task: TaskEnvelope,
        brief: CreativeBrief,
        actions: list[ActionRecord],
    ) -> PlanningArtifact:
        planning = self.invoke_tool(
            task,
            actions,
            self.PLAN_TOOL,
            prompt=planning_prompt(brief),
            output_type=PlanningArtifact,
        )
        assert isinstance(planning, PlanningArtifact)
        problems = _plan_problems(planning, brief)
        retries = 0
        while problems and retries < task.budget.max_retries:
            planning = self.invoke_tool(
                task,
                actions,
                self.PLAN_TOOL,
                prompt=planning_quality_repair_prompt(
                    brief, planning, "；".join(problems)
                ),
                output_type=PlanningArtifact,
            )
            assert isinstance(planning, PlanningArtifact)
            problems = _plan_problems(planning, brief)
            retries += 1
        if problems and task.budget.fallback_policy == "fail":
            raise ValueError(f"narrative plan rejected: {'；'.join(problems)}")
        return planning

    def _generate_script(
        self,
        task: TaskEnvelope,
        brief: CreativeBrief,
        planning: PlanningArtifact,
        actions: list[ActionRecord],
    ) -> ScriptArtifact:
        script = self.invoke_tool(
            task,
            actions,
            self.SCRIPT_TOOL,
            prompt=script_prompt(brief, planning),
            output_type=ScriptArtifact,
        )
        assert isinstance(script, ScriptArtifact)
        problems = _script_problems(script)
        retries = 0
        while problems and retries < task.budget.max_retries:
            script = self.invoke_tool(
                task,
                actions,
                self.SCRIPT_TOOL,
                prompt=script_quality_repair_prompt(
                    brief, planning, script, "；".join(problems)
                ),
                output_type=ScriptArtifact,
            )
            assert isinstance(script, ScriptArtifact)
            problems = _script_problems(script)
            retries += 1
        if problems and task.budget.fallback_policy == "fail":
            raise ValueError(f"narrative script rejected: {'；'.join(problems)}")
        return script


def _plan_problems(
    planning: PlanningArtifact,
    brief: CreativeBrief,
) -> list[str]:
    problems: list[str] = []
    profile = planning.video_profile
    if profile.requested != brief.video_profile:
        problems.append("video_profile.requested 必须与 CreativeBrief 一致")
    if brief.video_profile != "auto" and profile.resolved != brief.video_profile:
        problems.append("用户显式指定的 video profile 不得被 AI 改写")
    close_section_id = planning.sections[-1].section_id
    if not any(
        beat.section_id == close_section_id
        and beat.discourse_role in {"payoff", "callback"}
        for beat in planning.beats
    ):
        problems.append(
            "Close 中必须至少有一个 discourse_role 为 payoff 或 callback 的 Beat"
        )
    return problems


def _script_problems(script: ScriptArtifact) -> list[str]:
    problems: list[str] = []
    estimated_ms = estimate_duration_ms(script)
    if not 60_000 <= estimated_ms <= 120_000:
        problems.append(
            f"估算口播为 {estimated_ms / 1000:.1f}s，应落在 60–120s 的宽松窗口内"
        )
    emphasis_missing = [
        f"{segment.script_segment_id}:{word}"
        for segment in script.segments
        for word in segment.delivery_hint.emphasis_words
        if word not in segment.text
    ]
    if emphasis_missing:
        problems.append(f"这些重音词不在正文中：{emphasis_missing}")
    return problems
