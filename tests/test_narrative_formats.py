from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from tests.test_quality import sample_plan, sample_script
from ugc_harness.agents.instructions import (
    compose_opening_messages,
    instruction_name_for_task,
    system_prompt_for_task,
)
from ugc_harness.agents.narrative_agent import make_brief
from ugc_harness.harness.narrative_formats import (
    DRAMA_PACK,
    EXPLAINER_PACK,
    TUTORIAL_PACK,
    NarrativeFormatRegistry,
    default_narrative_format_registry,
)
from ugc_harness.harness.models import TaskEnvelope, TaskScope
from ugc_harness.content import (
    AudioRealizationSpec,
    ProductionShot,
    VisualRealizationSpec,
)
from ugc_harness.agents.narrative_agent.models import (
    DramaPlanningArtifact,
    NarrativePlanningArtifact,
    NarrativeScriptArtifact,
    TutorialPlanningArtifact,
)
from ugc_harness.agents.narrative_agent.shots import compile_explainer_shots


def test_default_registry_resolves_installed_formats() -> None:
    registry = default_narrative_format_registry()

    assert registry.resolve("explainer") is EXPLAINER_PACK
    assert registry.resolve("drama") is DRAMA_PACK
    assert registry.resolve("tutorial") is TUTORIAL_PACK
    assert registry.capability_tools == tuple(
        dict.fromkeys(
            (
                *EXPLAINER_PACK.capability_tools(),
                *DRAMA_PACK.capability_tools(),
                *TUTORIAL_PACK.capability_tools(),
            )
        )
    )


def test_brief_carries_the_requested_production_mode() -> None:
    brief = make_brief(topic="包粽子教程", production_mode="tutorial")

    assert brief.production_mode == "tutorial"


def test_pack_materializes_the_agent_contract_into_task_envelope() -> None:
    task = EXPLAINER_PACK.create_task(
        make_brief(topic="端午节"),
        state_version=3,
        input_hash="hash",
    )

    assert task.format_id == "explainer"
    assert task.allowed_tools == list(EXPLAINER_PACK.capability_tools())
    assert task.required_outputs == ["planning", "script", "shots"]
    prompt = system_prompt_for_task(task)
    assert "person/place/product" in prompt


def test_system_prompt_follows_the_task_step() -> None:
    explainer = EXPLAINER_PACK.create_task(
        make_brief(topic="端午节"),
        state_version=0,
        input_hash="hash",
    )
    assert instruction_name_for_task(explainer) == "narrative_explainer"
    assert "explainer workflow" in system_prompt_for_task(explainer)

    drama = DRAMA_PACK.create_task(
        make_brief(topic="端午节", production_mode="drama"),
        state_version=0,
        input_hash="hash",
    )
    assert instruction_name_for_task(drama) == "narrative_drama"

    tutorial = TUTORIAL_PACK.create_task(
        make_brief(topic="端午节", production_mode="tutorial"),
        state_version=0,
        input_hash="hash",
    )
    assert instruction_name_for_task(tutorial) == "narrative_tutorial"

    repair = EXPLAINER_PACK.create_repair_task(
        task_id="task_repair",
        scope=TaskScope(
            project_id=explainer.scope.project_id,
            target_refs=["shot:s1"],
        ),
        state_version=0,
        input_hash="hash",
        dependency_snapshot=[],
    )
    assert instruction_name_for_task(repair) == "shot_repair"
    assert "shot 局部修复" in system_prompt_for_task(repair)


def test_narrative_opening_splits_context_and_artifacts() -> None:
    messages = compose_opening_messages(
        system="explainer workflow",
        agent="narrative_agent",
        memory={
            "context": {"creative_brief": {"topic": "粽子"}},
            "artifacts": {"shots": [], "structure": None},
        },
        user_payload={"instruction": "首跑"},
    )
    assert [item["role"] for item in messages] == ["system", "user", "user"]
    assert "explainer workflow" in messages[0]["content"]
    assert "Skill 目录" in messages[0]["content"]
    assert "上下文" in messages[1]["content"]
    assert "叙事产物" in messages[2]["content"]

    assert (
        instruction_name_for_task(
            TaskEnvelope(
                task_id="t_voice",
                agent="voice_agent",
                goal="配音",
                scope=TaskScope(project_id="p"),
                based_on_state_version=0,
                allowed_tools=["voice.submit_candidate"],
                input_hash="h",
            )
        )
        == "voice"
    )
    assert (
        instruction_name_for_task(
            TaskEnvelope(
                task_id="t_refs",
                agent="references_agent",
                goal="参考图",
                scope=TaskScope(project_id="p"),
                based_on_state_version=0,
                allowed_tools=["references.submit_candidate"],
                input_hash="h",
            )
        )
        == "references"
    )
    assert (
        instruction_name_for_task(
            TaskEnvelope(
                task_id="t_shot_asset",
                agent="asset_stage",
                goal="出镜",
                scope=TaskScope(project_id="p"),
                based_on_state_version=0,
                allowed_tools=["asset.submit_candidate"],
                input_hash="h",
            )
        )
        == "shot_asset"
    )


@pytest.mark.parametrize(
    ("pack", "mode", "required_outputs", "tool_prefix"),
    [
        (
            DRAMA_PACK,
            "drama",
            ["world_state", "planning", "shots"],
            "narrative.",
        ),
        (
            TUTORIAL_PACK,
            "tutorial",
            ["world_state", "planning", "script", "shots"],
            "narrative.",
        ),
    ],
)
def test_future_pack_materializes_a_complete_task_contract(
    pack: object,
    mode: str,
    required_outputs: list[str],
    tool_prefix: str,
) -> None:
    task = pack.create_task(  # type: ignore[attr-defined]
        make_brief(topic="端午节", production_mode=mode),
        state_version=2,
        input_hash="hash",
    )

    assert task.format_id == mode
    assert task.required_outputs == required_outputs
    assert task.allowed_tools
    assert all(
        name.startswith(tool_prefix) or name == "narrative.submit_candidate"
        for name in task.allowed_tools
    )
    prompt = system_prompt_for_task(task)
    assert "world_state.entities" in prompt


def test_drama_pack_rejects_presenter_led_video_profiles() -> None:
    with pytest.raises(ValueError, match="requires video_profile"):
        DRAMA_PACK.create_task(
            make_brief(
                topic="端午节剧情",
                production_mode="drama",
                video_profile="a_roll",
            ),
            state_version=0,
            input_hash="hash",
        )


def test_tutorial_pack_is_installed_in_the_default_registry() -> None:
    default_registry = default_narrative_format_registry()
    extended_registry = NarrativeFormatRegistry(
        (EXPLAINER_PACK, DRAMA_PACK, TUTORIAL_PACK)
    )

    assert default_registry.resolve("drama") is DRAMA_PACK
    assert default_registry.resolve("tutorial") is TUTORIAL_PACK
    assert extended_registry.resolve("drama") is DRAMA_PACK
    assert extended_registry.resolve("tutorial") is TUTORIAL_PACK


def test_narrative_session_holds_identities_and_shots() -> None:
    from ugc_harness.mcp_servers.narrative import NarrativeTaskSession

    session = NarrativeTaskSession(
        brief=make_brief(topic="端午节", production_mode="drama"),
        format_id="drama",
    )
    assert session.identities is None
    assert session.shots is None
    assert session.format_id == "drama"


def test_planning_union_uses_planning_type_discriminator() -> None:
    value = sample_plan().model_dump(mode="json")
    parsed = TypeAdapter(NarrativePlanningArtifact).validate_python(value)

    assert parsed.planning_type == "explainer"
    with pytest.raises(ValidationError):
        TypeAdapter(NarrativePlanningArtifact).validate_python(
            {**value, "planning_type": "unknown"}
        )


def test_drama_schema_validates_world_and_scene_references() -> None:
    explainer = sample_plan()
    world = explainer.world_state.model_dump(mode="json")
    world["entities"].extend(
        [
            {
                "entity_id": "character_01",
                "name": "阿舟",
                "kind": "person",
                "narrative_role": "protagonist",
                "description": "学习包粽子的年轻人",
            },
            {
                "entity_id": "location_01",
                "name": "厨房",
                "kind": "place",
                "narrative_role": "primary_location",
                "description": "端午节家庭厨房",
            },
        ]
    )
    value = {
        "one_sentence_thesis": "一次失败的包粽子让人物理解了传承",
        "world_state": world,
        "video_profile": explainer.video_profile.model_dump(mode="json"),
        "premise": "主角尝试独自完成奶奶留下的粽子配方",
        "characters": [
            {
                "character_id": "character_01",
                "name": "阿舟",
                "description": "急于证明自己",
            }
        ],
        "scenes": [
            {
                "scene_id": "scene_01",
                "order": 1,
                "location_id": "location_01",
                "purpose": "建立失败与冲突",
                "character_ids": ["character_01"],
            },
            {
                "scene_id": "scene_02",
                "order": 2,
                "location_id": "location_01",
                "purpose": "完成选择与回响",
                "character_ids": ["character_01"],
            },
        ],
        "actions": [
            {
                "action_id": "action_01",
                "order": 1,
                "scene_id": "scene_01",
                "description": "粽叶散开，主角停住动作",
                "character_ids": ["character_01"],
            },
            {
                "action_id": "action_02",
                "order": 2,
                "scene_id": "scene_01",
                "description": "主角重新观察奶奶留下的折痕",
                "character_ids": ["character_01"],
            },
            {
                "action_id": "action_03",
                "order": 3,
                "scene_id": "scene_02",
                "description": "主角沿折痕重新包裹粽叶",
                "character_ids": ["character_01"],
            },
            {
                "action_id": "action_04",
                "order": 4,
                "scene_id": "scene_02",
                "description": "完整的粽子被放到旧照片旁",
                "character_ids": ["character_01"],
            },
        ],
    }

    assert DramaPlanningArtifact.model_validate(value).planning_type == "drama"
    value["actions"][0]["scene_id"] = "missing_scene"
    with pytest.raises(ValidationError, match="unknown scenes"):
        DramaPlanningArtifact.model_validate(value)


def test_tutorial_schema_and_script_are_step_addressable() -> None:
    explainer = sample_plan()
    planning = TutorialPlanningArtifact.model_validate(
        {
            "one_sentence_thesis": "用清晰动作展示粽子的完整制作过程",
            "world_state": explainer.world_state.model_dump(mode="json"),
            "video_profile": explainer.video_profile.model_dump(mode="json"),
            "objective": "完成一个可蒸制的四角粽",
            "materials": [{"material_id": "rice", "name": "糯米"}],
            "tools": ["棉线"],
            "steps": [
                {
                    "step_id": "step_01",
                    "order": 1,
                    "instruction": "折叠粽叶形成漏斗",
                    "visual_evidence": ["漏斗底部没有缝隙"],
                }
            ],
            "actions": [
                {
                    "action_id": "action_01",
                    "step_id": "step_01",
                    "description": "双手交叠并旋转粽叶",
                }
            ],
        }
    )
    script = TypeAdapter(NarrativeScriptArtifact).validate_python(
        {
            "script_type": "tutorial",
            "segments": [
                {
                    "explanation_segment_id": "explain_01",
                    "step_id": "step_01",
                    "placement": "after_action",
                    "text": "底部不漏米才算折叠到位。",
                }
            ],
        }
    )

    assert planning.planning_type == "tutorial"
    assert script.script_type == "tutorial"


def test_visual_and_audio_specs_are_discriminated_unions() -> None:
    visual = TypeAdapter(VisualRealizationSpec).validate_python(
        {
            "realization_type": "generated_scene",
            "scene_id": "scene_01",
            "character_ids": ["character_01"],
            "location_id": "location_01",
            "action_description": "角色将香囊递给同伴",
            "camera_instruction": "中景转手部特写",
            "continuity_constraints": ["香囊交接后归属同伴"],
            "generation_prompt": "端午街巷，两名角色完成香囊交接",
        }
    )
    audio = TypeAdapter(AudioRealizationSpec).validate_python(
        {
            "audio_mode": "embedded_in_video",
            "dialogue_lines": ["这个香囊送给你。"],
        }
    )

    assert visual.realization_type == "generated_scene"
    assert audio.audio_mode == "embedded_in_video"


def test_explainer_compiler_emits_the_common_shot_protocol() -> None:
    planning = sample_plan()
    shots = compile_explainer_shots(planning, sample_script(planning))
    shot = shots.shots[0]

    assert shot.visual.realization_type == "explainer"
    assert shot.audio.audio_mode == "external_narration"
    assert shot.timing.duration_driver == "narration"
    assert shot.payload.payload_type == "explainer"


def test_production_shot_rejects_cross_format_components() -> None:
    planning = sample_plan()
    shot = compile_explainer_shots(planning, sample_script(planning)).shots[0]
    value = shot.model_dump(mode="json")
    value["shot_kind"] = "drama"

    with pytest.raises(ValidationError, match="shot_kind"):
        ProductionShot.model_validate(value)
