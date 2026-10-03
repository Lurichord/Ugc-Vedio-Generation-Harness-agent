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
WRITE_IDENTITIES_TOOL = "narrative.write_identities"
WRITE_SECTIONS_TOOL = "narrative.write_sections"
WRITE_BEATS_TOOL = "narrative.write_beats"
WRITE_SCRIPT_TOOL = "narrative.write_script"
WRITE_WORLD_TOOL = "narrative.write_world"
WRITE_STORY_TOOL = "narrative.write_story"
WRITE_ACTIONS_TOOL = "narrative.write_actions"
WRITE_DEFINITION_TOOL = "narrative.write_definition"
WRITE_PROCEDURE_TOOL = "narrative.write_procedure"
WRITE_EXPLANATIONS_TOOL = "narrative.write_explanations"
WRITE_SHOTS_TOOL = "narrative.write_shots"
LIST_STATE_TOOL = "narrative.list_state"
REPAIR_SUBMIT_TOOL = "repair.submit_candidate"

SHARED_NARRATIVE_TOOLS = (
    WRITE_IDENTITIES_TOOL,
    WRITE_SHOTS_TOOL,
    LIST_STATE_TOOL,
    SUBMIT_CANDIDATE_TOOL,
)
EXPLAINER_TOOLS = (
    WRITE_IDENTITIES_TOOL,
    WRITE_SECTIONS_TOOL,
    WRITE_BEATS_TOOL,
    WRITE_SCRIPT_TOOL,
    WRITE_SHOTS_TOOL,
    LIST_STATE_TOOL,
    SUBMIT_CANDIDATE_TOOL,
)
DRAMA_TOOLS = (
    WRITE_IDENTITIES_TOOL,
    WRITE_WORLD_TOOL,
    WRITE_STORY_TOOL,
    WRITE_ACTIONS_TOOL,
    WRITE_SHOTS_TOOL,
    LIST_STATE_TOOL,
    SUBMIT_CANDIDATE_TOOL,
)
TUTORIAL_TOOLS = (
    WRITE_IDENTITIES_TOOL,
    WRITE_DEFINITION_TOOL,
    WRITE_PROCEDURE_TOOL,
    WRITE_EXPLANATIONS_TOOL,
    WRITE_SHOTS_TOOL,
    LIST_STATE_TOOL,
    SUBMIT_CANDIDATE_TOOL,
)
NARRATIVE_TOOLS = EXPLAINER_TOOLS


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

    _TOOLS = EXPLAINER_TOOLS

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
            goal="按层写出三节、节拍、口播和 ProductionShot",
            scope=TaskScope(project_id=project_id),
            based_on_state_version=state_version,
            format_id=self.format_id,
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
                "至少六镜，口播写在每镜 narration_text",
                "跨镜身份只使用身份册里的 binds",
                "Close 兑现 Hook",
                "输出通过 Pydantic Schema 和独立 Narrative Critic",
            ],
            budget=TaskBudget(
                max_steps=16,
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

    _TOOLS = DRAMA_TOOLS

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
            goal="按层写出世界、场次、动作和 ProductionShot",
            required_outputs=["world_state", "planning", "shots"],
            acceptance_criteria=[
                "至少两场、四镜；一镜一个主动作",
                "角色、场景和物品引用均存在于身份册",
                "Drama Shot 使用 generated_scene 与 embedded_in_video 音频",
                "输出通过 Drama Schema 和独立 Drama Critic",
            ],
            max_steps=16,
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

    _TOOLS = TUTORIAL_TOOLS

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
            goal="按层写出成品、步骤、讲解和 ProductionShot",
            required_outputs=["world_state", "planning", "script", "shots"],
            acceptance_criteria=[
                "每一步的输入状态能够由前一步输出满足",
                "关键操作具有明确的视觉证据和安全约束",
                "讲解不会替代或遮挡核心制作动作",
                "输出通过 Tutorial Schema 和独立 Tutorial Critic",
            ],
            max_steps=16,
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
            "goal": "按 instruction 微调圈中的 shot，不要重做整份规划",
            "scope": scope,
            "dependency_snapshot": dependency_snapshot,
            "allowed_tools": [REPAIR_SUBMIT_TOOL],
            "required_outputs": ["patches"],
            "forbidden_actions": [
                *task.forbidden_actions,
                "modify_outside_repair_scope",
                "rewrite_identity_names",
                "modify_readonly_neighbors",
            ],
            "acceptance_criteria": [
                "只改 editable 的 shot",
                "交回每一个可写 shot 的补丁",
                "shot_id / order / shot_kind / source_refs 不变",
            ],
            "budget": TaskBudget(
                max_steps=3,
                max_retries=1,
                fallback_policy="fail",
            ),
        }
    )


class NarrativeFormatRegistry:
    def __init__(self, packs: tuple[NarrativeFormatPack, ...]) -> None:
        self._packs = {pack.format_id: pack for pack in packs}
        if len(self._packs) != len(packs):
            raise ValueError("format pack ids must be unique")

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
        pack = self._packs.get(mode)
        if pack is None:
            installed = ", ".join(sorted(self._packs)) or "none"
            raise ValueError(
                f"Narrative format {mode!r} is not installed; "
                f"installed formats: {installed}"
            )
        return pack


EXPLAINER_PACK = ExplainerFormatPack()
DRAMA_PACK = DramaFormatPack()
TUTORIAL_PACK = TutorialFormatPack()


def default_narrative_format_registry() -> NarrativeFormatRegistry:
    return NarrativeFormatRegistry((EXPLAINER_PACK, DRAMA_PACK, TUTORIAL_PACK))
