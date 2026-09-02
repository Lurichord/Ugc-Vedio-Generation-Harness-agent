"""Live generic-agent narrative tests. Real choose_tool and real narrative MCP."""

from __future__ import annotations

from typing import Any

import pytest

from tests.fixtures.live import dump_json, live_settings
from ugc_harness.agents.narrative_agent import make_brief
from ugc_harness.agents.narrative_agent.models import (
    DramaPlanningArtifact,
    PlanningArtifact,
    TutorialPlanningArtifact,
)
from ugc_harness.harness.controller import NarrativeHarnessController, NarrativeHarnessRun
from ugc_harness.harness.dependencies import DependencyGraph
from ugc_harness.harness.narrative_formats import (
    DRAMA_PACK,
    EXPLAINER_PACK,
    SUBMIT_CANDIDATE_TOOL,
    TUTORIAL_PACK,
)
from ugc_harness.shared.llm import StructuredLLM

pytestmark = pytest.mark.live

_FORMATS = (
    (
        "explainer",
        "为什么单元测试能减少返工",
        "单元测试",
        EXPLAINER_PACK,
        "narrative.explainer.compile_shots",
    ),
    (
        "drama",
        "实习生第一天加班",
        "加班",
        DRAMA_PACK,
        "narrative.drama.compile_shots",
    ),
    (
        "tutorial",
        "家里包粽子",
        "粽子",
        TUTORIAL_PACK,
        "narrative.tutorial.compile_shots",
    ),
)


def _dump(run: NarrativeHarnessRun | None, error: str | None = None) -> str:
    payload: dict[str, Any] = {"error": error}
    if run is not None:
        payload["actions"] = [
            {"tool": item.tool, "result": item.result, "reason": item.reason}
            for item in run.record.agent_result.actions
        ]
        payload["status"] = run.record.agent_result.status
        payload["quality"] = run.artifact.quality.model_dump(mode="json")
        payload["evaluation"] = run.record.evaluation.model_dump(mode="json")
    return dump_json(payload)


def _blob(run: NarrativeHarnessRun) -> str:
    script = None if run.artifact.script is None else run.artifact.script.model_dump(mode="json")
    return dump_json(
        {
            "planning": run.artifact.planning.model_dump(mode="json"),
            "script": script,
        }
    )


def _succeeded(run: NarrativeHarnessRun, tool: str) -> bool:
    return any(
        item.tool == tool and item.result == "success"
        for item in run.record.agent_result.actions
    )


@pytest.mark.parametrize(
    ("production_mode", "topic", "needle", "pack", "compile_tool"),
    _FORMATS,
    ids=["explainer", "drama", "tutorial"],
)
def test_generic_agent_runs_narrative_format(
    production_mode: str,
    topic: str,
    needle: str,
    pack: Any,
    compile_tool: str,
) -> None:
    settings = live_settings()
    brief = make_brief(
        topic=topic,
        duration_seconds=90,
        production_mode=production_mode,  # type: ignore[arg-type]
    )
    run: NarrativeHarnessRun | None = None
    try:
        run = NarrativeHarnessController.from_mcp(
            StructuredLLM(settings),
            settings.model,
        ).run(brief)
    except Exception as exc:
        pytest.fail(_dump(run, str(exc)))

    allowed = set(pack.capability_tools())
    used = [item.tool for item in run.record.agent_result.actions]
    context = _dump(run)
    assert run.record.agent_result.status == "completed", context
    assert set(used) <= allowed, context
    assert _succeeded(run, compile_tool), context
    assert _succeeded(run, SUBMIT_CANDIDATE_TOOL), context
    assert run.artifact.quality.passed is True, context
    assert run.record.project_state.production.structure is not None, context

    shots = run.artifact.shots
    assert shots is not None and shots.shots, context
    planning = run.artifact.planning
    if isinstance(planning, PlanningArtifact):
        covered = {shot.payload.planned_beat_id for shot in shots.shots}
        expected = {beat.planned_beat_id for beat in planning.beats}
        assert covered == expected, context
    elif isinstance(planning, DramaPlanningArtifact):
        scenes = {shot.payload.scene_id for shot in shots.shots}
        expected_scenes = {scene.scene_id for scene in planning.scenes}
        assert scenes <= expected_scenes, context
        actions = {action.action_id for action in planning.actions}
        used_actions = {
            action_id
            for shot in shots.shots
            for action_id in shot.payload.action_ids
        }
        assert used_actions <= actions, context
        assert scenes, context
    elif isinstance(planning, TutorialPlanningArtifact):
        covered = {shot.payload.step_id for shot in shots.shots}
        expected = {step.step_id for step in planning.steps}
        assert covered == expected, context
    else:
        pytest.fail(f"unexpected planning type {type(planning)}\n{context}")

    graph = run.record.project_state.dependency_graph
    DependencyGraph(graph).validate_integrity()
    refs = set(graph.nodes)
    assert "artifact:narrative" not in refs, context
    assert any(ref.startswith("shot:") for ref in refs), context
    if production_mode == "explainer":
        assert any(ref.startswith("beat:") for ref in refs), context
        assert any(ref.startswith("utterance:") for ref in refs), context
    elif production_mode == "drama":
        assert any(ref.startswith("scene:") for ref in refs), context
    else:
        assert any(ref.startswith("step:") for ref in refs), context

    blob = _blob(run)
    assert needle in blob or topic in blob, (
        f"brief topic {topic!r} did not appear in planning/script\n"
        f"{context}\n{blob[:2000]}"
    )
