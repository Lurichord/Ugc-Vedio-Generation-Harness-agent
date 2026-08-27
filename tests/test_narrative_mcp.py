from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from ugc_harness.agents.narrative_agent import make_brief
from ugc_harness.harness.narrative_formats import DRAMA_PACK, EXPLAINER_PACK
from ugc_harness.harness.controller import NarrativeHarnessController
from ugc_harness.shared.llm import ModelToolCall
from ugc_harness.tools.mcp import StdioMCPServerConfig

SECTIONS_MODEL_TOOL = "narrative__explainer__plan_sections"
BEATS_MODEL_TOOL = "narrative__explainer__expand_beats"
SCRIPT_MODEL_TOOL = "narrative__explainer__write_script"
SHOTS_MODEL_TOOL = "narrative__explainer__compile_shots"
SUBMIT_MODEL_TOOL = "narrative__submit_candidate"
HAPPY_PATH = [
    SECTIONS_MODEL_TOOL,
    BEATS_MODEL_TOOL,
    SCRIPT_MODEL_TOOL,
    SHOTS_MODEL_TOOL,
    SUBMIT_MODEL_TOOL,
]
DRAMA_HAPPY_PATH = [
    "narrative__drama__plan_story",
    "narrative__drama__design_world",
    "narrative__drama__expand_scenes",
    "narrative__drama__compile_shots",
    SUBMIT_MODEL_TOOL,
]
TUTORIAL_HAPPY_PATH = [
    "narrative__tutorial__plan_steps",
    "narrative__tutorial__define_result",
    "narrative__tutorial__plan_steps",
    "narrative__tutorial__plan_explanations",
    "narrative__tutorial__compile_shots",
    SUBMIT_MODEL_TOOL,
]


class SequentialToolModel:
    """A deterministic model fixture; production models choose autonomously."""

    def __init__(self) -> None:
        self.selected: list[str] = []
        self.offered: list[list[str]] = []

    def choose_tool(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ModelToolCall:
        names = [tool["function"]["name"] for tool in tools]
        self.offered.append(names)
        name = next(step for step in HAPPY_PATH if step not in self.selected)
        self.selected.append(name)
        return ModelToolCall(
            call_id=f"call_{len(self.selected)}",
            name=name,
            arguments={},
        )


class ScriptedToolModel:
    """Replays a fixed tool-choice sequence to probe the agent loop."""

    def __init__(self, sequence: list[str]) -> None:
        self.sequence = list(sequence)
        self.selected: list[str] = []

    def choose_tool(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ModelToolCall:
        if len(self.selected) >= len(self.sequence):
            raise RuntimeError(f"scripted tool sequence exhausted: {messages[-1]}")
        name = self.sequence[len(self.selected)]
        self.selected.append(name)
        return ModelToolCall(
            call_id=f"call_{len(self.selected)}",
            name=name,
            arguments={},
        )


def _fixture_server() -> StdioMCPServerConfig:
    root = Path(__file__).resolve().parents[1]
    source = root / "src"
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(root), str(source)])
    env["PYTHONUNBUFFERED"] = "1"
    return StdioMCPServerConfig(
        command=sys.executable,
        args=("-m", "tests.fixtures.narrative_mcp_server"),
        cwd=root,
        env=env,
    )


def test_narrative_agent_runs_the_explainer_tool_chain() -> None:
    model = SequentialToolModel()
    run = NarrativeHarnessController.from_mcp(
        model,
        "fixture-model",
        server=_fixture_server(),
    ).run(make_brief(topic="stdio MCP narrative agent test"))

    assert run.artifact.quality.passed is True
    assert run.record.agent_result.artifact_refs
    state_description = run.record.project_state.description
    assert state_description is not None
    assert run.artifact.planning.world_state == state_description.world.state
    # The model saw the full allowed tool set on every step, not a forced
    # single-tool menu.
    assert model.offered == [HAPPY_PATH] * 5
    assert model.selected == HAPPY_PATH
    assert [action.tool for action in run.record.agent_result.actions] == [
        *EXPLAINER_PACK.capability_tools(),
    ]
    assert all(
        action.result == "success"
        for action in run.record.agent_result.actions
    )
    shots = run.artifact.shots
    assert shots is not None
    assert len(shots.shots) == len(run.artifact.planning.beats)
    covered = {shot.payload.planned_beat_id for shot in shots.shots}
    assert covered == {
        beat.planned_beat_id for beat in run.artifact.planning.beats
    }
    assert all(
        shot.audio.audio_mode == "external_narration"
        and shot.timing.duration_driver == "narration"
        for shot in shots.shots
    )
    graph = run.record.project_state.dependency_graph.nodes
    assert "artifact:narrative" in graph
    assert not any(ref.startswith("section:") for ref in graph)
    assert not any(ref.startswith("planned_beat:") for ref in graph)
    assert not any(ref.startswith("script_segment:") for ref in graph)


def test_narrative_agent_feeds_tool_errors_back_to_the_model() -> None:
    # The model first calls write_script before any plan exists. The MCP
    # server rejects it; the error is fed back into the conversation and the
    # model recovers instead of the harness aborting the task.
    model = ScriptedToolModel([SCRIPT_MODEL_TOOL, *HAPPY_PATH])
    run = NarrativeHarnessController.from_mcp(
        model,
        "fixture-model",
        server=_fixture_server(),
    ).run(make_brief(topic="stdio MCP narrative agent recovery test"))

    assert run.artifact.quality.passed is True
    assert model.selected == [SCRIPT_MODEL_TOOL, *HAPPY_PATH]
    results = [
        (action.tool, action.result)
        for action in run.record.agent_result.actions
    ]
    assert results == [
        (EXPLAINER_PACK.capability_tools()[2], "failed"),
        (EXPLAINER_PACK.capability_tools()[0], "success"),
        (EXPLAINER_PACK.capability_tools()[1], "success"),
        (EXPLAINER_PACK.capability_tools()[2], "success"),
        (EXPLAINER_PACK.capability_tools()[3], "success"),
        (EXPLAINER_PACK.capability_tools()[4], "success"),
    ]


def test_agent_can_submit_early_then_replan_from_the_tool_error() -> None:
    model = ScriptedToolModel([SUBMIT_MODEL_TOOL, *HAPPY_PATH])
    run = NarrativeHarnessController.from_mcp(
        model,
        "fixture-model",
        server=_fixture_server(),
    ).run(make_brief(topic="自主提交与修复测试"))

    assert run.record.evaluation.passed is True
    assert [
        (action.tool, action.result)
        for action in run.record.agent_result.actions
    ] == [
        ("narrative.submit_candidate", "failed"),
        ("narrative.explainer.plan_sections", "success"),
        ("narrative.explainer.expand_beats", "success"),
        ("narrative.explainer.write_script", "success"),
        ("narrative.explainer.compile_shots", "success"),
        ("narrative.submit_candidate", "success"),
    ]


def test_narrative_agent_rejects_tools_outside_the_envelope() -> None:
    model = ScriptedToolModel(["narrative__configure_task"])
    run_error: Exception | None = None
    try:
        NarrativeHarnessController.from_mcp(
            model,
            "fixture-model",
            server=_fixture_server(),
        ).run(make_brief(topic="stdio MCP narrative agent allow-list test"))
    except RuntimeError as exc:
        run_error = exc
    assert run_error is not None
    assert "unavailable tool" in str(run_error)


def test_narrative_agent_runs_the_drama_tool_chain_with_embedded_audio() -> None:
    model = ScriptedToolModel(DRAMA_HAPPY_PATH)
    run = NarrativeHarnessController.from_mcp(
        model,
        "fixture-model",
        server=_fixture_server(),
    ).run(make_brief(topic="端午节旧粽叶", production_mode="drama"))

    assert run.artifact.planning.planning_type == "drama"
    assert run.artifact.script is None
    assert run.record.evaluation.critic_id == "drama_critic"
    assert run.record.evaluation.passed is True
    assert run.record.transition.to_agent == "asset_agent"
    assert run.record.project_state.video.script_status == "not_required"
    assert run.record.project_state.video.voice_status == "not_required"
    assert run.record.project_state.video.editorial_status == "not_required"
    assert run.record.project_state.video.asset_status == "ready"
    assert model.selected == DRAMA_HAPPY_PATH
    assert [action.tool for action in run.record.agent_result.actions] == [
        "narrative.drama.plan_story",
        "narrative.drama.design_world",
        "narrative.drama.expand_scenes",
        "narrative.drama.compile_shots",
        "narrative.submit_candidate",
    ]
    shots = run.artifact.shots
    assert shots is not None
    assert all(
        shot.shot_kind == "drama"
        and shot.visual.realization_type == "generated_scene"
        and shot.audio.audio_mode == "embedded_in_video"
        and shot.timing.duration_driver == "generated_clip"
        for shot in shots.shots
    )
    graph = run.record.project_state.dependency_graph.nodes
    assert "artifact:narrative" in graph
    assert "character:character_azhou" not in graph
    assert "scene:scene_attempt" not in graph
    assert "action:action_01" not in graph


def test_narrative_agent_runs_the_tutorial_tools_with_mixed_audio() -> None:
    model = ScriptedToolModel(TUTORIAL_HAPPY_PATH)
    run = NarrativeHarnessController.from_mcp(
        model,
        "fixture-model",
        server=_fixture_server(),
    ).run(make_brief(topic="端午节四角粽制作", production_mode="tutorial"))

    assert run.artifact.planning.planning_type == "tutorial"
    assert run.artifact.script is not None
    assert run.record.evaluation.critic_id == "tutorial_critic"
    assert run.record.evaluation.passed is True
    assert run.record.transition.to_agent == "asset_agent"
    assert run.record.project_state.video.script_status == "passed"
    assert run.record.project_state.video.voice_status == "not_required"
    assert run.record.project_state.video.editorial_status == "not_required"
    assert run.record.project_state.video.asset_status == "ready"
    assert all(
        action.result == "success"
        for action in run.record.agent_result.actions
    )
    assert model.selected == TUTORIAL_HAPPY_PATH
    shots = run.artifact.shots
    assert shots is not None
    assert all(
        shot.shot_kind == "tutorial"
        and shot.visual.realization_type == "procedure_demo"
        and shot.audio.audio_mode == "mixed"
        and shot.timing.duration_driver == "demonstration_action"
        for shot in shots.shots
    )
    graph = run.record.project_state.dependency_graph.nodes
    assert "artifact:narrative" in graph
    assert not any(ref.startswith("step:") for ref in graph)
    assert not any(ref.startswith("action:") for ref in graph)


def test_agent_accepts_allow_listed_original_mcp_tool_names() -> None:
    model = ScriptedToolModel(
        [
            "narrative.tutorial.define_result",
            "narrative.tutorial.plan_steps",
            "narrative.tutorial.plan_explanations",
            "narrative.tutorial.compile_shots",
            "narrative.submit_candidate",
        ]
    )

    run = NarrativeHarnessController.from_mcp(
        model,
        "fixture-model",
        server=_fixture_server(),
    ).run(make_brief(topic="端午节制作教程", production_mode="tutorial"))

    assert run.record.evaluation.passed is True
    assert [item.tool for item in run.record.agent_result.actions] == model.selected
