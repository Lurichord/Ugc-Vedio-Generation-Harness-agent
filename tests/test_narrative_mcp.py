from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

from ugc_harness.agents.generic import StepDecision
from ugc_harness.agents.narrative_agent import make_brief
from ugc_harness.agents.skills import SKILL_ACTIVATE
from ugc_harness.harness.narrative_formats import (
    EXPLAINER_PACK,
    SUBMIT_CANDIDATE_TOOL,
    WRITE_ACTIONS_TOOL,
    WRITE_BEATS_TOOL,
    WRITE_DEFINITION_TOOL,
    WRITE_EXPLANATIONS_TOOL,
    WRITE_PROCEDURE_TOOL,
    WRITE_SCRIPT_TOOL,
    WRITE_SECTIONS_TOOL,
    WRITE_SHOTS_TOOL,
    WRITE_STORY_TOOL,
    WRITE_WORLD_TOOL,
)
from ugc_harness.harness.controller import NarrativeHarnessController
from ugc_harness.tools.mcp import StdioMCPServerConfig, mcp_tool_catalog

SUBMIT_MODEL_TOOL = SUBMIT_CANDIDATE_TOOL
HAPPY_PATH = [
    WRITE_SECTIONS_TOOL,
    WRITE_BEATS_TOOL,
    WRITE_SCRIPT_TOOL,
    WRITE_SHOTS_TOOL,
    SUBMIT_MODEL_TOOL,
]
DRAMA_HAPPY_PATH = [
    WRITE_WORLD_TOOL,
    WRITE_STORY_TOOL,
    WRITE_ACTIONS_TOOL,
    WRITE_SHOTS_TOOL,
    SUBMIT_MODEL_TOOL,
]
TUTORIAL_HAPPY_PATH = [
    WRITE_DEFINITION_TOOL,
    WRITE_PROCEDURE_TOOL,
    WRITE_EXPLANATIONS_TOOL,
    WRITE_SHOTS_TOOL,
    SUBMIT_MODEL_TOOL,
]
ALLOWED = list(EXPLAINER_PACK.capability_tools())


class SequentialToolModel:
    """A deterministic model fixture; production models choose autonomously."""

    def __init__(self) -> None:
        self.selected: list[str] = []
        self.offered: list[list[str]] = []

    def decide(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> StepDecision:
        del messages
        names = [str(tool["name"]) for tool in tools]
        self.offered.append(names)
        name = next(step for step in HAPPY_PATH if step not in self.selected)
        self.selected.append(name)
        return StepDecision(tool=name)


class ScriptedToolModel:
    """Replays a fixed tool-choice sequence to probe the agent loop."""

    def __init__(self, sequence: list[str]) -> None:
        self.sequence = list(sequence)
        self.selected: list[str] = []

    def decide(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> StepDecision:
        del tools
        if len(self.selected) >= len(self.sequence):
            raise RuntimeError(f"scripted tool sequence exhausted: {messages[-1]}")
        name = self.sequence[len(self.selected)]
        self.selected.append(name)
        return StepDecision(tool=name)


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


def test_narrative_mcp_tools_advertise_descriptions_and_parameters() -> None:
    from ugc_harness.mcp_servers.narrative import (
        CONFIGURE_TOOL,
        WRITE_IDENTITIES_TOOL,
        WRITE_SECTIONS_TOOL as PROD_WRITE_SECTIONS,
        WRITE_SHOTS_TOOL as PROD_WRITE_SHOTS,
        SUBMIT_TOOL,
        mcp,
    )

    catalog = {
        item["name"]: item
        for item in mcp_tool_catalog(mcp, exclude=frozenset({CONFIGURE_TOOL}))
    }
    assert WRITE_IDENTITIES_TOOL in catalog
    assert PROD_WRITE_SECTIONS in catalog
    assert PROD_WRITE_SHOTS in catalog
    assert SUBMIT_TOOL in catalog
    identities = catalog[WRITE_IDENTITIES_TOOL]
    assert "身份册" in identities["description"]
    shots = catalog[PROD_WRITE_SHOTS]
    assert "identities" in shots["parameters"]["properties"]
    assert "shots" in shots["parameters"]["properties"]
    assert "前置" in catalog[PROD_WRITE_SECTIONS]["description"]
    assert catalog[SUBMIT_TOOL]["description"]
    assert "properties" in catalog[SUBMIT_TOOL]["parameters"]
    for item in catalog.values():
        assert item["description"], item["name"]


def test_narrative_agent_runs_the_explainer_tool_chain() -> None:
    model = SequentialToolModel()
    run = NarrativeHarnessController.from_mcp(
        model,
        "fixture-model",
        server=_fixture_server(),
    ).run(make_brief(topic="stdio MCP narrative agent test"))

    assert run.artifact.quality.passed is True
    assert run.record.agent_result.artifact_refs
    production = run.record.project_state.production
    assert production.structure is not None
    assert [item.claim_id for item in production.world.claims] == [
        item.claim_id for item in run.artifact.planning.world_state.claims
    ]
    # The model saw the full allowed tool set on every step, not a forced
    # single-tool menu.
    offered = [SKILL_ACTIVATE, *ALLOWED]
    assert model.offered == [offered] * len(HAPPY_PATH)
    assert model.selected == HAPPY_PATH
    assert [action.tool for action in run.record.agent_result.actions] == HAPPY_PATH
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
    assert "artifact:narrative" not in graph
    assert any(ref.startswith("beat:") for ref in graph)
    assert not any(ref.startswith("section:") for ref in graph)
    assert any(ref.startswith("utterance:") for ref in graph)
    assert any(ref.startswith("shot:") for ref in graph)
    assert not any(ref.startswith("planned_beat:") for ref in graph)
    assert not any(ref.startswith("script_segment:") for ref in graph)


def test_narrative_agent_feeds_tool_errors_back_to_the_model() -> None:
    model = ScriptedToolModel(["narrative.write_identities", SUBMIT_MODEL_TOOL, *HAPPY_PATH])
    run = NarrativeHarnessController.from_mcp(
        model,
        "fixture-model",
        server=_fixture_server(),
    ).run(make_brief(topic="stdio MCP narrative agent recovery test"))

    assert run.artifact.quality.passed is True
    results = [
        (action.tool, action.result)
        for action in run.record.agent_result.actions
    ]
    assert results == [
        ("narrative.write_identities", "success"),
        (SUBMIT_MODEL_TOOL, "failed"),
        *[(name, "success") for name in HAPPY_PATH],
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
        *[(name, "success") for name in HAPPY_PATH],
    ]


def test_narrative_agent_rejects_tools_outside_the_envelope() -> None:
    model = ScriptedToolModel(["narrative.configure_task"])
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
    assert run.record.transition.to_agent == "references_agent"
    assert run.record.project_state.evaluation.stages.script == "not_required"
    assert run.record.project_state.evaluation.stages.voice == "not_required"
    assert run.record.project_state.evaluation.stages.editorial == "not_required"
    assert run.record.project_state.evaluation.stages.references == "ready"
    assert run.record.project_state.evaluation.stages.asset == "pending"
    assert model.selected == DRAMA_HAPPY_PATH
    assert [action.tool for action in run.record.agent_result.actions] == [
        *DRAMA_HAPPY_PATH,
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
    assert "artifact:narrative" not in graph
    assert "cast:character_azhou" in graph
    assert "place:location_kitchen" in graph
    assert "scene:scene_attempt" in graph
    assert "action:action_01" not in graph
    assert any(ref.startswith("shot:") for ref in graph)
    assert "character:character_azhou" not in graph


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
    assert run.record.transition.to_agent == "references_agent"
    assert run.record.project_state.evaluation.stages.script == "passed"
    assert run.record.project_state.evaluation.stages.voice == "pending"
    assert run.record.project_state.evaluation.stages.editorial == "not_required"
    assert run.record.project_state.evaluation.stages.references == "ready"
    assert run.record.project_state.evaluation.stages.asset == "pending"
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
    assert "artifact:narrative" not in graph
    assert any(ref.startswith("step:") for ref in graph)
    assert any(ref.startswith("prop:") for ref in graph)
    assert not any(ref.startswith("action:") for ref in graph)
    assert any(ref.startswith("shot:") for ref in graph)


def test_agent_accepts_allow_listed_original_mcp_tool_names() -> None:
    model = ScriptedToolModel(list(TUTORIAL_HAPPY_PATH))

    run = NarrativeHarnessController.from_mcp(
        model,
        "fixture-model",
        server=_fixture_server(),
    ).run(make_brief(topic="端午节制作教程", production_mode="tutorial"))

    assert run.record.evaluation.passed is True
    assert [item.tool for item in run.record.agent_result.actions] == model.selected


def test_write_shots_requires_prior_layer_on_first_run() -> None:
    from tests.fixtures.narrative_mcp_server import (
        _canned_beat_plan,
        _canned_script,
        _canned_section_plan,
        _canned_shots,
    )
    from ugc_harness.mcp_servers import narrative as narrative_mcp

    brief = make_brief(topic="首跑必须先写上一层")
    shots = [item.model_dump(mode="json") for item in _canned_shots("explainer").shots]
    narrative_mcp.configure_task(brief.model_dump(mode="json"), model="test")
    try:
        with pytest.raises(ValueError, match="前置未满足：先 narrative.write_script"):
            narrative_mcp.write_shots(shots=shots)
        with pytest.raises(ValueError, match="前置未满足：先 narrative.write_sections"):
            narrative_mcp.write_beats(
                beats=_canned_beat_plan().model_dump(mode="json")
            )
        narrative_mcp.write_sections(
            sections=_canned_section_plan().model_dump(mode="json")
        )
        narrative_mcp.write_beats(beats=_canned_beat_plan().model_dump(mode="json"))
        narrative_mcp.write_script(script=_canned_script().model_dump(mode="json"))
        result = narrative_mcp.write_shots(shots=shots)
        assert result["ok"] is True
        assert narrative_mcp._session is not None
        assert narrative_mcp._session.identities is not None
        assert narrative_mcp._session.script is not None
        assert narrative_mcp._session.shots is not None
        assert len(narrative_mcp._session.shots.shots) == len(shots)
    finally:
        narrative_mcp._session = None
