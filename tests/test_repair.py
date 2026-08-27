from pathlib import Path

import pytest

from tests.fixtures.tool_models import CyclingToolModel
from tests.test_editorial import FakeGenerator as EditorialGenerator
from tests.test_editorial import _editorial_tool_model, _plan
from tests.test_narrative_controller import (
    FakeGenerator as NarrativeGenerator,
    narrative_controller_from_generator,
)
from tests.test_voice import make_voice_controller
from ugc_harness.agents.narrative_agent import make_brief
from ugc_harness.harness.dependencies import DependencyGraph, NodeCommit
from ugc_harness.harness.editorial_controller import EditorialHarnessController
from ugc_harness.harness.repair import RepairScheduler, select_repair_commits
from ugc_harness.harness.voice_controller import VoiceHarnessController


def _complete_state(tmp_path: Path):
    return _complete_context(tmp_path)[0]


def _complete_context(tmp_path: Path):
    narrative_run = narrative_controller_from_generator(
        NarrativeGenerator(), "fake-model"
    ).run(make_brief(topic="局部修复测试", duration_seconds=90))
    narrative = narrative_run.artifact
    voice_run = make_voice_controller().run(
        narrative, tmp_path, narrative_run.record.project_state
    )
    voice = voice_run.artifact
    plan = _plan(
        [beat.beat_id for beat in voice.realized_beats],
        narrative.brief.project_id,
        next(
            beat.beat_id
            for beat in voice.realized_beats
            if beat.planned_beat_id == "pb04"
        ),
    )
    editorial_run = EditorialHarnessController.from_generator(
        EditorialGenerator(plan), "fake-model", tool_model=_editorial_tool_model()
    ).run(narrative, voice, voice_run.record.project_state)
    return editorial_run.record.project_state, narrative, voice, plan


def _invalidate_narrative_boundary(state) -> DependencyGraph:
    graph = DependencyGraph(state.dependency_graph)
    node = state.dependency_graph.nodes["artifact:narrative"]
    graph.commit_batch(
        task_id="invalidate_narrative_boundary",
        produced_by="narrative_agent",
        commits=[
            NodeCommit(
                "artifact:narrative",
                node.kind,
                {"edited": True},
                tuple(node.depends_on),
            )
        ],
    )
    state.dependency_graph.nodes["artifact:narrative"].status = "stale"
    return graph


def test_scheduler_creates_only_the_first_local_repair_frontier(
    tmp_path: Path,
) -> None:
    state = _complete_state(tmp_path)
    _invalidate_narrative_boundary(state)

    plan = RepairScheduler().plan(state, ["artifact:editorial"])

    assert plan.blockers == []
    assert len(plan.tasks) == 1
    task = plan.tasks[0]
    assert task.agent == "narrative_agent"
    assert task.scope.target_refs == ["artifact:narrative"]
    assert task.scope.script_segment_ids == []
    assert task.scope.beat_ids == []
    assert task.dependency_snapshot


def test_scheduler_moves_to_voice_after_narrative_branch_is_repaired(
    tmp_path: Path,
) -> None:
    state = _complete_state(tmp_path)
    graph = _invalidate_narrative_boundary(state)
    first = RepairScheduler().plan(state, ["artifact:editorial"])
    targets = set(first.tasks[0].scope.target_refs)
    graph.commit_batch(
        task_id="repair_narrative",
        produced_by="narrative_agent",
        commits=[
            NodeCommit(
                ref,
                state.dependency_graph.nodes[ref].kind,
                {"repaired": ref},
                tuple(state.dependency_graph.nodes[ref].depends_on),
            )
            for ref in sorted(targets)
        ],
    )

    second = RepairScheduler().plan(state, ["artifact:editorial"])

    assert len(second.tasks) == 1
    assert second.tasks[0].agent == "voice_agent"
    assert any(
        ref.startswith("voice_segment:")
        for ref in second.tasks[0].scope.target_refs
    )


def test_scheduler_blocks_a_locked_stale_branch(tmp_path: Path) -> None:
    state = _complete_state(tmp_path)
    _invalidate_narrative_boundary(state)
    state.dependency_graph.nodes["artifact:narrative"].locked = True

    plan = RepairScheduler().plan(state, ["artifact:editorial"])

    assert plan.tasks == []
    assert plan.blockers[0].ref == "artifact:narrative"


def test_repair_commit_rejects_changes_outside_scope(tmp_path: Path) -> None:
    state = _complete_state(tmp_path)
    graph = DependencyGraph(state.dependency_graph)
    task = RepairScheduler().plan(state, ["artifact:editorial"])
    assert task.complete is True

    voice_refs = sorted(
        ref
        for ref in state.dependency_graph.nodes
        if ref.startswith("voice_segment:")
    )
    target_ref, unauthorized_ref = voice_refs[:2]
    existing = state.dependency_graph.nodes[target_ref]
    from ugc_harness.harness.models import TaskBudget, TaskEnvelope, TaskScope

    repair_task = TaskEnvelope(
        task_id="repair_scope_test",
        agent="voice_agent",
        goal="repair",
        scope=TaskScope(
            project_id=state.video.project_id,
            target_refs=[target_ref],
        ),
        based_on_state_version=state.video.state_version,
        allowed_tools=["voice.create_plan"],
        budget=TaskBudget(),
        input_hash="test",
    )
    with pytest.raises(ValueError, match="REPAIR_SCOPE_VIOLATION"):
        select_repair_commits(
            graph,
            [
                NodeCommit(
                    target_ref,
                    existing.kind,
                    "target change",
                    tuple(existing.depends_on),
                ),
                NodeCommit(
                    unauthorized_ref,
                    state.dependency_graph.nodes[unauthorized_ref].kind,
                    "unauthorized change",
                    tuple(state.dependency_graph.nodes[unauthorized_ref].depends_on),
                ),
            ],
            repair_task,
        )


def test_editorial_controller_executes_scoped_repair_task(
    tmp_path: Path,
) -> None:
    state, narrative, voice, editorial_plan = _complete_context(tmp_path)
    visual_ref = "visual_requirement:vr01"
    state.dependency_graph.nodes[visual_ref].status = "stale"
    state.dependency_graph.nodes["artifact:editorial"].status = "stale"
    repair_plan = RepairScheduler().plan(state, ["artifact:editorial"])
    task = repair_plan.tasks[0]

    run = EditorialHarnessController.from_generator(
        EditorialGenerator(editorial_plan),
        "fake-model",
        tool_model=_editorial_tool_model(),
    ).run(narrative, voice, state, task)

    updated = run.record.project_state
    assert updated.dependency_graph.nodes[visual_ref].status == "current"
    assert updated.dependency_graph.nodes["artifact:editorial"].status == "current"
    assert run.record.transition.to_agent == "repair_scheduler"
    history = updated.trajectory.phases["editorial"].tasks
    assert history[-1].task_kind == "repair"
    assert history[-1].task.scope.target_refs == task.scope.target_refs
    assert history[-1].graph_update.committed is True
