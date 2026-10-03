from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from tests.test_quality import sample_plan, sample_script
from ugc_harness.agents.narrative_agent.assemble import (
    assemble_candidate,
    fill_generation_prompt,
    with_resolved_profile,
)
from ugc_harness.agents.narrative_agent.memory import summarize_identities, summarize_shots
from ugc_harness.agents.narrative_agent.models import (
    BeatPlanArtifact,
    CreativeBrief,
    DramaActionPlanArtifact,
    DramaPlanningArtifact,
    DramaStoryArtifact,
    DramaWorldArtifact,
    NarrativeCandidate,
    NarrativeIdentitiesArtifact,
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
from ugc_harness.content import ProductionShot


mcp = MCPServer("test-narrative", version="3.1.0")
_brief: CreativeBrief | None = None
_format_id = "explainer"
_identities: NarrativeIdentitiesArtifact | None = None
_shots: ShotPlanArtifact | None = None
_section_plan: SectionPlanArtifact | None = None
_beat_plan: BeatPlanArtifact | None = None
_script: ScriptArtifact | TutorialScriptArtifact | None = None
_world: DramaWorldArtifact | None = None
_story: DramaStoryArtifact | None = None
_action_plan: DramaActionPlanArtifact | None = None
_definition: TutorialDefinitionArtifact | None = None
_procedure: TutorialProcedureArtifact | None = None


def _sample_explainer() -> tuple[object, object]:
    baseline_path = os.getenv("NARRATIVE_EXPLAINER_ARTIFACT")
    if baseline_path:
        payload = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
        from ugc_harness.agents.narrative_agent.models import (
            PlanningArtifact,
            ScriptArtifact,
        )

        return (
            PlanningArtifact.model_validate(payload["planning"]),
            ScriptArtifact.model_validate(payload["script"]),
        )
    plan = sample_plan()
    return plan, sample_script(plan)


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


def plan_tutorial_explanations(
    problems: list[str] | None = None,
) -> TutorialScriptArtifact:
    del problems
    return TutorialScriptArtifact.model_validate(
        {
            "segments": [
                {
                    "explanation_segment_id": "te01",
                    "step_id": "step_fold",
                    "placement": "during_action",
                    "text": "关键不是卷得紧，而是让底部折角完全闭合。",
                },
                {
                    "explanation_segment_id": "te02",
                    "step_id": "step_fill",
                    "placement": "after_action",
                    "text": "米不要装满，留下的这一指空间就是封口余量。",
                },
                {
                    "explanation_segment_id": "te03",
                    "step_id": "step_tie",
                    "placement": "during_action",
                    "text": "棉线要跨过容易张开的两个方向，固定后再打结。",
                },
            ]
        }
    )


def _canned_section_plan() -> SectionPlanArtifact:
    plan, _ = _sample_explainer()
    return SectionPlanArtifact(
        narrative_pattern=plan.narrative_pattern,
        one_sentence_thesis=plan.one_sentence_thesis,
        world_state=plan.world_state,
        video_profile=plan.video_profile,
        sections=plan.sections,
    )


def _canned_beat_plan() -> BeatPlanArtifact:
    plan, _ = _sample_explainer()
    return BeatPlanArtifact(beats=plan.beats)


def _canned_script() -> ScriptArtifact:
    _, script = _sample_explainer()
    return script


def _canned_identities(format_id: str) -> NarrativeIdentitiesArtifact:
    if format_id == "drama":
        world = _sample_drama_world()
        return NarrativeIdentitiesArtifact(
            one_sentence_thesis=world.one_sentence_thesis,
            world_state=world.world_state,
            video_profile=world.video_profile,
            characters=world.characters,
        )
    if format_id == "tutorial":
        definition = _sample_tutorial_definition()
        return NarrativeIdentitiesArtifact(
            one_sentence_thesis=definition.one_sentence_thesis,
            world_state=definition.world_state,
            video_profile=definition.video_profile,
            objective=definition.objective,
            materials=definition.materials,
            tools=definition.tools,
            coverage_requirements=definition.coverage_requirements,
        )
    plan, _ = _sample_explainer()
    return NarrativeIdentitiesArtifact(
        one_sentence_thesis=plan.one_sentence_thesis,
        world_state=plan.world_state,
        video_profile=plan.video_profile,
    )


def _canned_shots(format_id: str) -> ShotPlanArtifact:
    if format_id == "drama":
        planning = DramaPlanningArtifact.from_parts(
            _sample_drama_world(),
            _sample_drama_story(),
            _sample_drama_actions(),
        )
        return compile_drama_shots(planning)
    if format_id == "tutorial":
        definition = _sample_tutorial_definition()
        procedure = _sample_tutorial_procedure()
        planning = TutorialPlanningArtifact.from_parts(definition, procedure)
        return compile_tutorial_shots(planning, plan_tutorial_explanations())
    plan, script = _sample_explainer()
    return compile_explainer_shots(plan, script)


def _reset_layers() -> None:
    global _identities, _shots, _section_plan, _beat_plan, _script
    global _world, _story, _action_plan, _definition, _procedure
    _identities = None
    _shots = None
    _section_plan = None
    _beat_plan = None
    _script = None
    _world = None
    _story = None
    _action_plan = None
    _definition = None
    _procedure = None


def _layers() -> dict[str, Any]:
    return {
        "section_plan": _section_plan,
        "beat_plan": _beat_plan,
        "script": _script,
        "world": _world,
        "story": _story,
        "action_plan": _action_plan,
        "definition": _definition,
        "procedure": _procedure,
    }


def _store_identities(book: NarrativeIdentitiesArtifact) -> NarrativeIdentitiesArtifact:
    global _identities
    if _brief is not None:
        book = with_resolved_profile(book, _brief, _format_id)
    _identities = book
    return book


@mcp.tool(name="narrative.configure_task", structured_output=True)
def configure_task(
    brief: dict[str, Any],
    model: str,
    generation_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    global _brief, _format_id, _identities, _shots
    _brief = CreativeBrief.model_validate(brief)
    _format_id = str(brief.get("production_mode") or "explainer")
    _reset_layers()
    context = generation_context or {}
    seed_identities = context.get("seed_identities")
    if isinstance(seed_identities, dict):
        _store_identities(NarrativeIdentitiesArtifact.model_validate(seed_identities))
    seed_shots = context.get("seed_shots")
    if isinstance(seed_shots, list) and seed_shots:
        _shots = ShotPlanArtifact(
            shots=[ProductionShot.model_validate(item) for item in seed_shots]
        )
    return {
        "project_id": brief["project_id"],
        "model": model,
        "format_id": _format_id,
        "status": "configured",
    }


@mcp.tool(name="narrative.write_identities", structured_output=True)
def write_identities(identities: dict[str, Any] | None = None) -> dict[str, Any]:
    if identities:
        book = NarrativeIdentitiesArtifact.model_validate(identities)
    else:
        book = _canned_identities(_format_id)
    return summarize_identities(_store_identities(book))


@mcp.tool(name="narrative.write_sections", structured_output=True)
def write_sections(sections: dict[str, Any] | None = None) -> dict[str, Any]:
    global _section_plan
    plan = (
        SectionPlanArtifact.model_validate(sections)
        if sections
        else _canned_section_plan()
    )
    _section_plan = plan
    _store_identities(
        NarrativeIdentitiesArtifact(
            one_sentence_thesis=plan.one_sentence_thesis,
            world_state=plan.world_state,
            video_profile=plan.video_profile,
        )
    )
    return {
        "ok": True,
        "sections": [
            {"section_id": item.section_id, "role": item.role, "goal": item.goal}
            for item in plan.sections
        ],
    }


@mcp.tool(name="narrative.write_beats", structured_output=True)
def write_beats(beats: dict[str, Any] | None = None) -> dict[str, Any]:
    global _beat_plan
    if _section_plan is None and beats is None:
        write_sections()
    plan = BeatPlanArtifact.model_validate(beats) if beats else _canned_beat_plan()
    _beat_plan = plan
    return {"ok": True, "count": len(plan.beats)}


@mcp.tool(name="narrative.write_script", structured_output=True)
def write_script(script: dict[str, Any] | None = None) -> dict[str, Any]:
    global _script
    if _beat_plan is None and script is None:
        write_beats()
    artifact = ScriptArtifact.model_validate(script) if script else _canned_script()
    _script = artifact
    return {"ok": True, "segments": len(artifact.segments)}


@mcp.tool(name="narrative.write_world", structured_output=True)
def write_world(world: dict[str, Any] | None = None) -> dict[str, Any]:
    global _world
    artifact = (
        DramaWorldArtifact.model_validate(world) if world else _sample_drama_world()
    )
    _world = artifact
    _store_identities(
        NarrativeIdentitiesArtifact(
            one_sentence_thesis=artifact.one_sentence_thesis,
            world_state=artifact.world_state,
            video_profile=artifact.video_profile,
            characters=list(artifact.characters),
        )
    )
    return summarize_identities(_identities) if _identities is not None else {"ok": True}


@mcp.tool(name="narrative.write_story", structured_output=True)
def write_story(story: dict[str, Any] | None = None) -> dict[str, Any]:
    global _story
    if _world is None and story is None:
        write_world()
    artifact = (
        DramaStoryArtifact.model_validate(story) if story else _sample_drama_story()
    )
    _story = artifact
    return {"ok": True, "scenes": len(artifact.scenes)}


@mcp.tool(name="narrative.write_actions", structured_output=True)
def write_actions(actions: dict[str, Any] | None = None) -> dict[str, Any]:
    global _action_plan
    if _story is None and actions is None:
        write_story()
    artifact = (
        DramaActionPlanArtifact.model_validate(actions)
        if actions
        else _sample_drama_actions()
    )
    _action_plan = artifact
    return {"ok": True, "count": len(artifact.actions)}


@mcp.tool(name="narrative.write_definition", structured_output=True)
def write_definition(definition: dict[str, Any] | None = None) -> dict[str, Any]:
    global _definition
    artifact = (
        TutorialDefinitionArtifact.model_validate(definition)
        if definition
        else _sample_tutorial_definition()
    )
    _definition = artifact
    _store_identities(
        NarrativeIdentitiesArtifact(
            one_sentence_thesis=artifact.one_sentence_thesis,
            world_state=artifact.world_state,
            video_profile=artifact.video_profile,
            objective=artifact.objective,
            materials=list(artifact.materials),
            tools=list(artifact.tools),
            coverage_requirements=list(artifact.coverage_requirements),
        )
    )
    return summarize_identities(_identities) if _identities is not None else {"ok": True}


@mcp.tool(name="narrative.write_procedure", structured_output=True)
def write_procedure(procedure: dict[str, Any] | None = None) -> dict[str, Any]:
    global _procedure
    if _definition is None and procedure is None:
        write_definition()
    artifact = (
        TutorialProcedureArtifact.model_validate(procedure)
        if procedure
        else _sample_tutorial_procedure()
    )
    _procedure = artifact
    return {"ok": True, "steps": len(artifact.steps)}


@mcp.tool(name="narrative.write_explanations", structured_output=True)
def write_explanations(explanations: dict[str, Any] | None = None) -> dict[str, Any]:
    global _script
    if _procedure is None and explanations is None:
        write_procedure()
    artifact = (
        TutorialScriptArtifact.model_validate(explanations)
        if explanations
        else plan_tutorial_explanations()
    )
    _script = artifact
    return {"ok": True, "segments": len(artifact.segments)}


@mcp.tool(name="narrative.write_shots", structured_output=True)
def write_shots(
    shots: list[dict[str, Any]] | None = None,
    identities: dict[str, Any] | None = None,
    merge: bool = True,
) -> dict[str, Any]:
    global _identities, _shots
    if identities:
        _store_identities(NarrativeIdentitiesArtifact.model_validate(identities))
    if shots:
        incoming = [
            fill_generation_prompt(ProductionShot.model_validate(item)) for item in shots
        ]
        if merge and _shots is not None:
            by_id = {item.shot_id: item for item in _shots.shots}
            for item in incoming:
                by_id[item.shot_id] = item
            ordered = sorted(by_id.values(), key=lambda item: item.order)
        else:
            ordered = incoming
        _shots = ShotPlanArtifact(shots=ordered)
    elif identities is None and shots is None:
        if _identities is None:
            if _format_id == "drama":
                write_world()
            elif _format_id == "tutorial":
                write_definition()
            else:
                write_sections()
        _shots = _canned_shots(_format_id)
    if _identities is None:
        raise ValueError("前置未满足：先写身份层，或在本调用带 identities")
    if _shots is None:
        raise ValueError("candidate is incomplete: shots")
    return {
        "ok": True,
        "identities": summarize_identities(_identities),
        "shots": summarize_shots(_shots),
    }


@mcp.tool(name="narrative.list_state", structured_output=True)
def list_state() -> dict[str, Any]:
    return {
        "format_id": _format_id,
        "identities": (
            summarize_identities(_identities)
            if _identities is not None
            else {"ok": True, "identities": [], "count": 0}
        ),
        "layers": {name: value is not None for name, value in _layers().items()},
        "shots": (
            summarize_shots(_shots)
            if _shots is not None
            else {"ok": True, "spine": [], "count": 0}
        ),
    }


@mcp.tool(name="narrative.submit_candidate", structured_output=True)
def submit_candidate() -> NarrativeCandidate:
    if _identities is None:
        raise ValueError("candidate is incomplete: identities")
    if _shots is None:
        raise ValueError("candidate is incomplete: shots")
    brief = _brief or CreativeBrief.model_validate(
        {
            "project_id": "fixture",
            "topic": "测试",
            "target": {"duration_target_ms": 90_000},
            "audience": {},
            "communication": {"goal": "测试"},
            "production_mode": _format_id,
        }
    )
    return assemble_candidate(
        brief=brief,
        format_id=_format_id,  # type: ignore[arg-type]
        identities=_identities,
        shots=_shots,
        layers=_layers(),
    )


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
