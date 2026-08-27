from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from ..content import NarrativeFormatId, ProductionMode
from ..agents.narrative_agent.models import (
    CreativeBrief,
    DramaPlanningArtifact,
    PlanningArtifact,
    TutorialPlanningArtifact,
)
from .models import DependencySnapshot, TaskBudget, TaskEnvelope, TaskScope


SUBMIT_CANDIDATE_TOOL = "narrative.submit_candidate"


class NarrativeFormatPack(Protocol):
    """Harness strategy that materializes one format into a TaskEnvelope."""

    format_id: NarrativeFormatId
    planning_schema: type[BaseModel]

    def capability_tools(self) -> tuple[str, ...]: ...

    def create_task(
        self,
        brief: CreativeBrief,
        *,
        state_version: int,
        input_hash: str,
    ) -> TaskEnvelope: ...

    def create_repair_task(
        self,
        *,
        task_id: str,
        scope: TaskScope,
        state_version: int,
        input_hash: str,
        dependency_snapshot: list[DependencySnapshot],
    ) -> TaskEnvelope: ...


class ExplainerFormatPack:
    format_id: NarrativeFormatId = "explainer"
    planning_schema: type[BaseModel] = PlanningArtifact

    _TOOLS = (
        "narrative.explainer.plan_sections",
        "narrative.explainer.expand_beats",
        "narrative.explainer.write_script",
        "narrative.explainer.compile_shots",
        SUBMIT_CANDIDATE_TOOL,
    )

    def capability_tools(self) -> tuple[str, ...]:
        return self._TOOLS

    def create_task(
        self,
        brief: CreativeBrief,
        *,
        state_version: int,
        input_hash: str,
    ) -> TaskEnvelope:
        return self._create_task(
            project_id=brief.project_id,
            state_version=state_version,
            input_hash=input_hash,
        )

    def _create_task(
        self,
        *,
        project_id: str,
        state_version: int,
        input_hash: str,
    ) -> TaskEnvelope:
        return TaskEnvelope(
            task_id=f"task_narrative_{project_id}_v{state_version}",
            agent="narrative_agent",
            goal="生成可验收的 Section、PlannedBeat、口播 Script 与 ProductionShot",
            scope=TaskScope(project_id=project_id),
            based_on_state_version=state_version,
            format_id=self.format_id,
            agent_instructions=(
                "你正在运行 explainer workflow。目标是产出完整的世界状态、"
                "Section 骨架、Planned Beats、外部口播 Script 和 ProductionShot。"
                "请根据消息历史自主规划、选择并重复调用任意允许的 MCP tool。"
                "工具错误会直接返回给你，请自行判断修改哪里。确认候选产物完整后调用"
                "narrative.submit_candidate；不要伪造工具结果。"
            ),
            allowed_tools=list(self._TOOLS),
            required_outputs=["planning", "script", "shots"],
            forbidden_actions=[
                "modify_voice",
                "modify_visuals",
                "modify_assets",
                "modify_timeline",
                "render_video",
            ],
            acceptance_criteria=[
                "Section 顺序为 hook、body、close",
                "每个 PlannedBeat 均有口播覆盖",
                "Close 兑现 Hook",
                "输出通过 Pydantic Schema 和独立 Narrative Critic",
            ],
            budget=TaskBudget(
                max_steps=8,
                max_retries=1,
                fallback_policy="use_best_available",
            ),
            input_hash=input_hash,
        )

    def create_repair_task(
        self,
        *,
        task_id: str,
        scope: TaskScope,
        state_version: int,
        input_hash: str,
        dependency_snapshot: list[DependencySnapshot],
    ) -> TaskEnvelope:
        task = self._create_task(
            project_id=scope.project_id,
            state_version=state_version,
            input_hash=input_hash,
        )
        return _create_repair_task(
            task,
            task_id=task_id,
            scope=scope,
            dependency_snapshot=dependency_snapshot,
        )


class DramaFormatPack:
    """Harness contract and capability whitelist for drama."""

    format_id: NarrativeFormatId = "drama"
    planning_schema: type[BaseModel] = DramaPlanningArtifact

    _TOOLS = (
        "narrative.drama.design_world",
        "narrative.drama.plan_story",
        "narrative.drama.expand_scenes",
        "narrative.drama.compile_shots",
        SUBMIT_CANDIDATE_TOOL,
    )

    def capability_tools(self) -> tuple[str, ...]:
        return self._TOOLS

    def create_task(
        self,
        brief: CreativeBrief,
        *,
        state_version: int,
        input_hash: str,
    ) -> TaskEnvelope:
        if brief.video_profile not in {"auto", "b_roll"}:
            raise ValueError(
                "drama format requires video_profile 'auto' or 'b_roll'"
            )
        return self._create_task(
            project_id=brief.project_id,
            state_version=state_version,
            input_hash=input_hash,
        )

    def _create_task(
        self,
        *,
        project_id: str,
        state_version: int,
        input_hash: str,
    ) -> TaskEnvelope:
        return _create_format_task(
            pack=self,
            project_id=project_id,
            state_version=state_version,
            input_hash=input_hash,
            goal="生成角色驱动、可连续生成的剧情规划与 ProductionShot",
            instructions=(
                "你正在运行 drama workflow。先建立角色、场景、物品和连续性约束，"
                "规划故事、表演场景并编译生成式视频 Shot。你可以根据当前消息历史自主"
                "选择、重复调用或修订任意工具，不存在 Harness 规定的调用顺序。剧情片段"
                "的声音由视频模型随画面产生，不要生成外部口播。候选完整后主动调用"
                "narrative.submit_candidate。"
            ),
            required_outputs=["world_state", "planning", "shots"],
            acceptance_criteria=[
                "人物目标、行动、反应和情绪变化形成因果链",
                "角色、场景和物品引用均存在于 World State",
                "Drama Shot 使用 generated_scene 与 embedded_in_video 音频",
                "输出通过 Drama Schema 和独立 Drama Critic",
            ],
            max_steps=10,
        )

    def create_repair_task(
        self,
        *,
        task_id: str,
        scope: TaskScope,
        state_version: int,
        input_hash: str,
        dependency_snapshot: list[DependencySnapshot],
    ) -> TaskEnvelope:
        return _create_repair_task(
            self._create_task(
                project_id=scope.project_id,
                state_version=state_version,
                input_hash=input_hash,
            ),
            task_id=task_id,
            scope=scope,
            dependency_snapshot=dependency_snapshot,
        )


class TutorialFormatPack:
    """Harness contract and capability whitelist for procedure tutorials."""

    format_id: NarrativeFormatId = "tutorial"
    planning_schema: type[BaseModel] = TutorialPlanningArtifact

    _TOOLS = (
        "narrative.tutorial.define_result",
        "narrative.tutorial.plan_steps",
        "narrative.tutorial.plan_explanations",
        "narrative.tutorial.compile_shots",
        SUBMIT_CANDIDATE_TOOL,
    )

    def capability_tools(self) -> tuple[str, ...]:
        return self._TOOLS

    def create_task(
        self,
        brief: CreativeBrief,
        *,
        state_version: int,
        input_hash: str,
    ) -> TaskEnvelope:
        return self._create_task(
            project_id=brief.project_id,
            state_version=state_version,
            input_hash=input_hash,
        )

    def _create_task(
        self,
        *,
        project_id: str,
        state_version: int,
        input_hash: str,
    ) -> TaskEnvelope:
        return _create_format_task(
            pack=self,
            project_id=project_id,
            state_version=state_version,
            input_hash=input_hash,
            goal="生成以制作步骤为主、讲解按需穿插的教程规划与 ProductionShot",
            instructions=(
                "你正在运行 tutorial workflow。请自主选择工具来定义目标成品、材料、"
                "操作步骤、补充讲解和 Procedure Shot；工具可以重复调用，不存在 Harness"
                "规定的顺序。不要假设每一个步骤都需要口播。候选完整后主动调用"
                "narrative.submit_candidate。"
            ),
            required_outputs=["world_state", "planning", "script", "shots"],
            acceptance_criteria=[
                "每一步的输入状态能够由前一步输出满足",
                "关键操作具有明确的视觉证据和安全约束",
                "讲解不会替代或遮挡核心制作动作",
                "输出通过 Tutorial Schema 和独立 Tutorial Critic",
            ],
            max_steps=10,
        )

    def create_repair_task(
        self,
        *,
        task_id: str,
        scope: TaskScope,
        state_version: int,
        input_hash: str,
        dependency_snapshot: list[DependencySnapshot],
    ) -> TaskEnvelope:
        return _create_repair_task(
            self._create_task(
                project_id=scope.project_id,
                state_version=state_version,
                input_hash=input_hash,
            ),
            task_id=task_id,
            scope=scope,
            dependency_snapshot=dependency_snapshot,
        )


def _create_format_task(
    *,
    pack: NarrativeFormatPack,
    project_id: str,
    state_version: int,
    input_hash: str,
    goal: str,
    instructions: str,
    required_outputs: list[str],
    acceptance_criteria: list[str],
    max_steps: int,
) -> TaskEnvelope:
    return TaskEnvelope(
        task_id=f"task_narrative_{project_id}_v{state_version}",
        agent="narrative_agent",
        goal=goal,
        scope=TaskScope(project_id=project_id),
        based_on_state_version=state_version,
        format_id=pack.format_id,
        agent_instructions=instructions,
        allowed_tools=list(pack.capability_tools()),
        required_outputs=required_outputs,
        forbidden_actions=[
            "modify_voice",
            "modify_visuals",
            "modify_assets",
            "modify_timeline",
            "render_video",
        ],
        acceptance_criteria=acceptance_criteria,
        budget=TaskBudget(
            max_steps=max_steps,
            max_retries=1,
            fallback_policy="use_best_available",
        ),
        input_hash=input_hash,
    )


def _create_repair_task(
    task: TaskEnvelope,
    *,
    task_id: str,
    scope: TaskScope,
    dependency_snapshot: list[DependencySnapshot],
) -> TaskEnvelope:
    return task.model_copy(
        update={
            "task_id": task_id,
            "goal": "局部重建失效的 narrative 节点，并保持修复范围之外的内容不变",
            "scope": scope,
            "dependency_snapshot": dependency_snapshot,
            "forbidden_actions": [
                *task.forbidden_actions,
                "modify_outside_repair_scope",
                "consume_stale_dependency",
                "overwrite_locked_node",
            ],
            "acceptance_criteria": [
                "所有 target_refs 重新变为 current",
                "scope 外节点的语义 hash 不变",
                "输出通过对应格式的 Narrative Critic",
            ],
        }
    )


class NarrativeFormatRegistry:
    def __init__(
        self,
        packs: tuple[NarrativeFormatPack, ...],
        *,
        auto_default: NarrativeFormatId = "explainer",
    ) -> None:
        self._packs = {pack.format_id: pack for pack in packs}
        if len(self._packs) != len(packs):
            raise ValueError("format pack ids must be unique")
        if auto_default not in self._packs:
            raise ValueError("auto_default must reference an installed format pack")
        self.auto_default = auto_default

    @property
    def capability_tools(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                tool
                for pack in self._packs.values()
                for tool in pack.capability_tools()
            )
        )

    def resolve(self, mode: ProductionMode | str) -> NarrativeFormatPack:
        format_id = self.auto_default if mode == "auto" else mode
        pack = self._packs.get(format_id)
        if pack is None:
            installed = ", ".join(sorted(self._packs)) or "none"
            raise ValueError(
                f"Narrative format {format_id!r} is not installed; "
                f"installed formats: {installed}"
            )
        return pack


EXPLAINER_PACK = ExplainerFormatPack()
DRAMA_PACK = DramaFormatPack()
TUTORIAL_PACK = TutorialFormatPack()


def default_narrative_format_registry() -> NarrativeFormatRegistry:
    return NarrativeFormatRegistry((EXPLAINER_PACK, DRAMA_PACK, TUTORIAL_PACK))
