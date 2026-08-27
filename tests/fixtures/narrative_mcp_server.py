from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from tests.test_quality import sample_plan, sample_script
from ugc_harness.agents.narrative_agent.models import (
    BeatPlanArtifact,
    DramaActionPlanArtifact,
    DramaPlanningArtifact,
    DramaStoryArtifact,
    DramaWorldArtifact,
    NarrativeCandidate,
    PlanningArtifact,
    ScriptArtifact,
    SectionPlanArtifact,
    ShotPlanArtifact,
    TutorialDefinitionArtifact,
    TutorialPlanningArtifact,
    TutorialProcedureArtifact,
    TutorialScriptArtifact,
)
from ugc_harness.agents.narrative_agent.shots import (
    compile_drama_shots,
    compile_explainer_shots,
    compile_tutorial_shots,
)


mcp = MCPServer("test-narrative", version="2.0.0")
_section_plan: SectionPlanArtifact | None = None
_planning: PlanningArtifact | None = None
_script: ScriptArtifact | None = None
_shots: ShotPlanArtifact | None = None
_format_id = "explainer"
_drama_world: DramaWorldArtifact | None = None
_drama_story: DramaStoryArtifact | None = None
_drama_actions: DramaActionPlanArtifact | None = None
_drama_planning: DramaPlanningArtifact | None = None
_tutorial_definition: TutorialDefinitionArtifact | None = None
_tutorial_procedure: TutorialProcedureArtifact | None = None
_tutorial_planning: TutorialPlanningArtifact | None = None
_tutorial_script: TutorialScriptArtifact | None = None


def _sample_explainer() -> tuple[PlanningArtifact, ScriptArtifact]:
    baseline_path = os.getenv("NARRATIVE_EXPLAINER_ARTIFACT")
    if baseline_path:
        payload = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
        return (
            PlanningArtifact.model_validate(payload["planning"]),
            ScriptArtifact.model_validate(payload["script"]),
        )
    plan = sample_plan()
    return plan, sample_script(plan)


@mcp.tool(name="narrative.configure_task", structured_output=True)
def configure_task(
    brief: dict[str, Any],
    model: str,
    generation_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    global _section_plan, _planning, _script, _shots, _format_id
    global _drama_world, _drama_story, _drama_actions, _drama_planning
    global _tutorial_definition, _tutorial_procedure, _tutorial_planning
    global _tutorial_script
    _section_plan = None
    _planning = None
    _script = None
    _shots = None
    _format_id = str(brief.get("production_mode") or "explainer")
    if _format_id == "auto":
        _format_id = "explainer"
    _drama_world = None
    _drama_story = None
    _drama_actions = None
    _drama_planning = None
    _tutorial_definition = None
    _tutorial_procedure = None
    _tutorial_planning = None
    _tutorial_script = None
    return {
        "project_id": brief["project_id"],
        "model": model,
        "format_id": _format_id,
        "status": "configured",
    }


@mcp.tool(name="narrative.submit_candidate", structured_output=True)
def submit_candidate() -> NarrativeCandidate:
    if _format_id == "drama":
        if _drama_world is None or _drama_planning is None or _shots is None:
            raise ValueError("drama candidate is incomplete")
        return NarrativeCandidate(
            world_state=_drama_world.world_state,
            planning=_drama_planning,
            shots=_shots,
        )
    if _format_id == "tutorial":
        if _tutorial_planning is None or _tutorial_script is None or _shots is None:
            raise ValueError(
                "tutorial candidate is incomplete: "
                f"planning={_tutorial_planning is not None}, "
                f"script={_tutorial_script is not None}, shots={_shots is not None}"
            )
        return NarrativeCandidate(
            world_state=_tutorial_planning.world_state,
            planning=_tutorial_planning,
            script=_tutorial_script,
            shots=_shots,
        )
    if _planning is None or _script is None or _shots is None:
        raise ValueError("explainer candidate is incomplete")
    return NarrativeCandidate(
        world_state=_planning.world_state,
        planning=_planning,
        script=_script,
        shots=_shots,
    )


def _sample_drama_world() -> DramaWorldArtifact:
    explainer = sample_plan()
    world = explainer.world_state.model_dump(mode="json")
    world["entities"].extend(
        [
            {
                "entity_id": "character_azhou",
                "name": "阿舟",
                "kind": "person",
                "narrative_role": "protagonist",
                "description": "第一次独自包粽子的年轻人",
            },
            {
                "entity_id": "location_kitchen",
                "name": "老厨房",
                "kind": "place",
                "narrative_role": "primary_location",
                "description": "端午清晨、有旧木桌的家庭厨房",
            },
        ]
    )
    return DramaWorldArtifact.model_validate(
        {
            "one_sentence_thesis": "传承不是复制答案，而是在失败中理解动作背后的耐心",
            "world_state": world,
            "video_profile": explainer.video_profile.model_dump(mode="json"),
            "characters": [
                {
                    "character_id": "character_azhou",
                    "name": "阿舟",
                    "description": "二十多岁，短发，白色棉麻衬衫，动作略显生疏",
                    "dramatic_objective": "独自完成奶奶教过的四角粽",
                    "appearance_constraints": ["白色棉麻衬衫", "短发"],
                    "voice_constraints": ["年轻男声", "对白自然克制"],
                }
            ],
        }
    )


def _sample_drama_story() -> DramaStoryArtifact:
    return DramaStoryArtifact.model_validate(
        {
            "premise": "阿舟试图凭记忆包出奶奶的四角粽，失败后从旧折痕中找到方法。",
            "scenes": [
                {
                    "scene_id": "scene_attempt",
                    "order": 1,
                    "location_id": "location_kitchen",
                    "purpose": "建立目标并让第一次尝试失败",
                    "character_ids": ["character_azhou"],
                    "emotional_turn": "自信变为挫败",
                    "continuity_constraints": ["桌面有散开的粽叶和糯米"],
                },
                {
                    "scene_id": "scene_understanding",
                    "order": 2,
                    "location_id": "location_kitchen",
                    "purpose": "人物理解折痕并完成粽子",
                    "character_ids": ["character_azhou"],
                    "emotional_turn": "焦躁变为平静和笃定",
                    "continuity_constraints": ["仍是同一清晨和同一张木桌"],
                },
            ],
        }
    )


def _sample_drama_actions() -> DramaActionPlanArtifact:
    descriptions = [
        ("scene_attempt", "阿舟快速卷起粽叶，糯米从底部漏出", "我明明记得是这样。"),
        ("scene_attempt", "棉线一拉，粽叶完全散开", "怎么又散了？"),
        ("scene_attempt", "阿舟停下手，看见旧粽叶上反复压出的折痕", ""),
        ("scene_understanding", "他沿旧折痕慢慢旋转粽叶，底部严密合拢", "原来不是用力。"),
        ("scene_understanding", "阿舟装米、压实并按顺序折回叶片", "是要顺着它。"),
        ("scene_understanding", "完整四角粽放在旧照片旁，阿舟轻轻笑了", "奶奶，我会了。"),
    ]
    return DramaActionPlanArtifact.model_validate(
        {
            "actions": [
                {
                    "action_id": f"action_{index:02d}",
                    "order": index,
                    "scene_id": scene_id,
                    "description": description,
                    "character_ids": ["character_azhou"],
                    "objective": "推进人物从失败到理解的变化",
                    "reaction": "动作结束后保留自然反应",
                    "dialogue_lines": [dialogue] if dialogue else [],
                    "state_changes": [f"drama continuity revision {index}"],
                    "camera_instruction": "手部近景与人物反应中景自然切换",
                    "ambient_audio": "厨房环境声、粽叶摩擦声",
                    "target_duration_ms": 15000,
                }
                for index, (scene_id, description, dialogue) in enumerate(
                    descriptions,
                    start=1,
                )
            ]
        }
    )


@mcp.tool(name="narrative.drama.design_world", structured_output=True)
def design_drama_world(problems: list[str] | None = None) -> DramaWorldArtifact:
    global _drama_world
    _drama_world = _sample_drama_world()
    return _drama_world


@mcp.tool(name="narrative.drama.plan_story", structured_output=True)
def plan_drama_story(problems: list[str] | None = None) -> DramaStoryArtifact:
    global _drama_story
    _drama_story = _sample_drama_story()
    return _drama_story


@mcp.tool(name="narrative.drama.expand_scenes", structured_output=True)
def expand_drama_scenes(
    problems: list[str] | None = None,
) -> DramaActionPlanArtifact:
    global _drama_actions, _drama_planning
    if _drama_world is None or _drama_story is None:
        raise RuntimeError("narrative.drama.plan_story must succeed first")
    _drama_actions = _sample_drama_actions()
    _drama_planning = DramaPlanningArtifact.from_parts(
        _drama_world,
        _drama_story,
        _drama_actions,
    )
    return _drama_actions


@mcp.tool(name="narrative.drama.compile_shots", structured_output=True)
def compile_drama_fixture_shots(
    problems: list[str] | None = None,
) -> ShotPlanArtifact:
    global _shots
    if _drama_planning is None:
        raise RuntimeError("narrative.drama.expand_scenes must succeed first")
    _shots = compile_drama_shots(_drama_planning)
    return _shots


def _sample_tutorial_definition() -> TutorialDefinitionArtifact:
    plan = sample_plan()
    return TutorialDefinitionArtifact.model_validate(
        {
            "one_sentence_thesis": "用粽叶折出严密漏斗并控制米量，就能包出不漏米的四角粽",
            "world_state": plan.world_state.model_dump(mode="json"),
            "video_profile": plan.video_profile.model_dump(mode="json"),
            "objective": "完成一个包裹紧实、四角清楚且不漏米的传统粽子",
            "materials": [
                {"material_id": "mat_leaf", "name": "处理好的粽叶", "quantity": "2片"},
                {"material_id": "mat_rice", "name": "浸泡糯米", "quantity": "适量"},
                {"material_id": "mat_string", "name": "棉线", "quantity": "1段"},
            ],
            "tools": ["剪刀", "勺子"],
            "coverage_requirements": ["漏斗底部无孔", "米量不过满", "棉线固定不松散"],
        }
    )


def _sample_tutorial_procedure() -> TutorialProcedureArtifact:
    return TutorialProcedureArtifact.model_validate(
        {
            "steps": [
                {
                    "step_id": "step_fold",
                    "order": 1,
                    "instruction": "叠放粽叶并旋转成无孔漏斗",
                    "input_state": "两片粽叶平整叠放",
                    "expected_result": "漏斗底部完全闭合",
                    "visual_evidence": ["底部折角没有透光缝隙"],
                    "common_mistakes": ["卷得太松导致底部开口"],
                    "safety_constraints": [],
                },
                {
                    "step_id": "step_fill",
                    "order": 2,
                    "instruction": "装入糯米并压出折叶空间",
                    "input_state": "粽叶漏斗已闭合",
                    "expected_result": "米面低于叶口并压实",
                    "visual_evidence": ["米面距离叶口约一指宽"],
                    "common_mistakes": ["装得过满无法封口"],
                    "safety_constraints": [],
                },
                {
                    "step_id": "step_tie",
                    "order": 3,
                    "instruction": "折回叶片并用棉线固定",
                    "input_state": "糯米已压实且留有封口空间",
                    "expected_result": "四角成形且轻晃不漏米",
                    "visual_evidence": ["四角轮廓清楚", "轻晃时没有米粒掉出"],
                    "common_mistakes": ["棉线只绕中间导致两端松开"],
                    "safety_constraints": ["使用剪刀时刀口远离手指"],
                },
            ],
            "actions": [
                {
                    "action_id": "action_fold",
                    "step_id": "step_fold",
                    "description": "双手叠放粽叶，沿三分之一处旋转形成漏斗",
                    "subject_ref": "mat_leaf",
                    "critical_detail": "镜头看清底部折角闭合",
                    "target_duration_ms": 25000,
                },
                {
                    "action_id": "action_fill",
                    "step_id": "step_fill",
                    "description": "用勺加入糯米，轻压并保留叶口空间",
                    "subject_ref": "mat_rice",
                    "critical_detail": "展示正确米量高度",
                    "target_duration_ms": 25000,
                },
                {
                    "action_id": "action_tie",
                    "step_id": "step_tie",
                    "description": "折回长叶片包住米面，绕线并打结",
                    "subject_ref": "mat_string",
                    "critical_detail": "展示折叶顺序和绕线受力位置",
                    "target_duration_ms": 30000,
                },
            ],
        }
    )


@mcp.tool(name="narrative.tutorial.define_result", structured_output=True)
def define_tutorial_result(problems: list[str] | None = None) -> TutorialDefinitionArtifact:
    global _tutorial_definition, _tutorial_planning
    _tutorial_definition = _sample_tutorial_definition()
    if _tutorial_procedure is not None:
        _tutorial_planning = TutorialPlanningArtifact.from_parts(
            _tutorial_definition, _tutorial_procedure
        )
    return _tutorial_definition


@mcp.tool(name="narrative.tutorial.plan_steps", structured_output=True)
def plan_tutorial_steps(problems: list[str] | None = None) -> TutorialProcedureArtifact:
    global _tutorial_definition, _tutorial_procedure, _tutorial_planning
    if _tutorial_definition is None:
        _tutorial_definition = _sample_tutorial_definition()
    _tutorial_procedure = _sample_tutorial_procedure()
    if _tutorial_definition is not None:
        _tutorial_planning = TutorialPlanningArtifact.from_parts(
            _tutorial_definition, _tutorial_procedure
        )
    return _tutorial_procedure


@mcp.tool(name="narrative.tutorial.plan_explanations", structured_output=True)
def plan_tutorial_explanations(problems: list[str] | None = None) -> TutorialScriptArtifact:
    global _tutorial_planning, _tutorial_script
    if _tutorial_definition is None or _tutorial_procedure is None:
        raise RuntimeError("tutorial planning drafts are incomplete")
    _tutorial_planning = TutorialPlanningArtifact.from_parts(
        _tutorial_definition, _tutorial_procedure
    )
    _tutorial_script = TutorialScriptArtifact.model_validate(
        {
            "segments": [
                {"explanation_segment_id": "te01", "step_id": "step_fold", "placement": "during_action", "text": "关键不是卷得紧，而是让底部折角完全闭合。"},
                {"explanation_segment_id": "te02", "step_id": "step_fill", "placement": "after_action", "text": "米不要装满，留下的这一指空间就是封口余量。"},
                {"explanation_segment_id": "te03", "step_id": "step_tie", "placement": "during_action", "text": "棉线要跨过容易张开的两个方向，固定后再打结。"},
            ]
        }
    )
    return _tutorial_script


@mcp.tool(name="narrative.tutorial.compile_shots", structured_output=True)
def compile_tutorial_fixture_shots(problems: list[str] | None = None) -> ShotPlanArtifact:
    global _shots
    if _tutorial_planning is None or _tutorial_script is None:
        raise RuntimeError("tutorial explanations must succeed first")
    _shots = compile_tutorial_shots(_tutorial_planning, _tutorial_script)
    return _shots


@mcp.tool(name="narrative.explainer.plan_sections", structured_output=True)
def plan_sections(problems: list[str] | None = None) -> SectionPlanArtifact:
    global _section_plan
    plan, _ = _sample_explainer()
    _section_plan = SectionPlanArtifact.model_validate(
        {
            key: value
            for key, value in plan.model_dump(mode="json").items()
            if key != "beats"
        }
    )
    return _section_plan


@mcp.tool(name="narrative.explainer.expand_beats", structured_output=True)
def expand_beats(problems: list[str] | None = None) -> BeatPlanArtifact:
    global _planning
    if _section_plan is None:
        raise RuntimeError("narrative.explainer.plan_sections must succeed first")
    _planning, _ = _sample_explainer()
    return BeatPlanArtifact.model_validate(
        {"beats": _planning.model_dump(mode="json")["beats"]}
    )


@mcp.tool(name="narrative.explainer.write_script", structured_output=True)
def write_script(problems: list[str] | None = None) -> ScriptArtifact:
    global _script
    if _planning is None:
        raise RuntimeError("narrative.explainer.expand_beats must succeed first")
    _, _script = _sample_explainer()
    return _script


@mcp.tool(name="narrative.explainer.compile_shots", structured_output=True)
def compile_shots(problems: list[str] | None = None) -> ShotPlanArtifact:
    global _shots
    if _planning is None or _script is None:
        raise RuntimeError("narrative.explainer.write_script must succeed first")
    _shots = compile_explainer_shots(_planning, _script)
    return _shots


if __name__ == "__main__":
    mcp.run(transport="stdio")
