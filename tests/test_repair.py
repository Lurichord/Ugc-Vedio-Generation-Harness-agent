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
from ugc_harness.harness.models import ProjectState, RuntimeContext, TaskEnvelope, VideoState
from ugc_harness.harness.repair import (
    RepairScheduler,
    drive_repair,
    invalidate_subgraph,
    load_project_state,
    select_repair_commits,
    write_project_state,
)
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


def _content_refs(state, *prefixes: str) -> list[str]:
    return sorted(
        ref for ref in state.dependency_graph.nodes if ref.startswith(prefixes)
    )


def commit_repair_targets(project_dir: Path, task: TaskEnvelope) -> None:
    state = load_project_state(project_dir)
    _commit_task_targets(state, task)
    write_project_state(project_dir, state)


def _commit_task_targets(state, task: TaskEnvelope) -> None:
    graph = DependencyGraph(state.dependency_graph)
    graph.commit_batch(
        task_id=task.task_id,
        produced_by=task.agent,
        commits=[
            NodeCommit(
                ref,
                state.dependency_graph.nodes[ref].kind,
                {"repaired": ref},
                tuple(state.dependency_graph.nodes[ref].depends_on),
            )
            for ref in task.scope.target_refs
        ],
    )


def _tiny_project_state() -> ProjectState:
    from tests.test_intake import _tiny_graph

    return ProjectState(
        runtime_context=RuntimeContext(),
        video=VideoState(project_id="demo", state_version=2, narrative_status="ready"),
        dependency_graph=_tiny_graph(),
    )


def _invalidate_beat(state) -> tuple[DependencyGraph, str]:
    beat_ref = _content_refs(state, "beat:")[0]
    graph = DependencyGraph(state.dependency_graph)
    node = state.dependency_graph.nodes[beat_ref]
    graph.commit_batch(
        task_id="invalidate_beat",
        produced_by="narrative_agent",
        commits=[
            NodeCommit(
                beat_ref,
                node.kind,
                {"edited": True},
                tuple(node.depends_on),
            )
        ],
    )
    state.dependency_graph.nodes[beat_ref].status = "stale"
    return graph, beat_ref


def test_scheduler_creates_only_the_first_local_repair_frontier(
    tmp_path: Path,
) -> None:
    state = _complete_state(tmp_path)
    _, beat_ref = _invalidate_beat(state)
    desired = _content_refs(state, "audio:")

    plan = RepairScheduler().plan(state, desired)

    assert plan.blockers == []
    assert len(plan.tasks) == 1
    task = plan.tasks[0]
    assert task.agent == "narrative_agent"
    assert beat_ref in task.scope.target_refs
    assert task.input_hash


def test_scheduler_moves_to_voice_after_narrative_branch_is_repaired(
    tmp_path: Path,
) -> None:
    state = _complete_state(tmp_path)
    graph, _ = _invalidate_beat(state)
    desired = _content_refs(state, "audio:")
    first = RepairScheduler().plan(state, desired)
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

    second = RepairScheduler().plan(state, desired)

    assert len(second.tasks) == 1
    assert second.tasks[0].agent == "voice_agent"
    assert all(
        ref.startswith("audio:")
        for ref in second.tasks[0].scope.target_refs
    )


def test_scheduler_blocks_a_locked_stale_branch(tmp_path: Path) -> None:
    state = _complete_state(tmp_path)
    _, beat_ref = _invalidate_beat(state)
    state.dependency_graph.nodes[beat_ref].locked = True

    plan = RepairScheduler().plan(state, _content_refs(state, "audio:"))

    assert plan.tasks == []
    assert plan.blockers[0].ref == beat_ref


def test_repair_commit_rejects_changes_outside_scope(tmp_path: Path) -> None:
    state = _complete_state(tmp_path)
    graph = DependencyGraph(state.dependency_graph)
    task = RepairScheduler().plan(state, _content_refs(state, "audio:"))
    assert task.complete is True

    voice_refs = _content_refs(state, "audio:")
    target_ref, unauthorized_ref = voice_refs[:2]
    existing = state.dependency_graph.nodes[target_ref]
    from ugc_harness.harness.models import TaskBudget, TaskEnvelope, TaskScope

    repair_task = TaskEnvelope(
        task_id="repair_scope_test",
        agent="voice_agent",
        goal="repair",
        scope=TaskScope(
            project_id=state.context.project_id,
            target_refs=[target_ref],
        ),
        based_on_state_version=state.context.state_version,
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


def test_repair_commit_allows_unchanged_outputs_outside_scope() -> None:
    state = _tiny_project_state()
    graph = DependencyGraph(state.dependency_graph)
    from ugc_harness.harness.models import TaskBudget, TaskEnvelope, TaskScope

    repair_task = TaskEnvelope(
        task_id="repair_carry_test",
        agent="narrative_agent",
        goal="repair",
        scope=TaskScope(
            project_id=state.context.project_id,
            target_refs=["utterance:ss3"],
        ),
        based_on_state_version=state.context.state_version,
        allowed_tools=["narrative.draft"],
        budget=TaskBudget(),
        input_hash="test",
    )
    selected = select_repair_commits(
        graph,
        [
            NodeCommit("utterance:ss3", "utterance", "line three rewritten"),
            NodeCommit("cast:host", "cast", "host"),
        ],
        repair_task,
    )
    assert [item.ref for item in selected] == ["utterance:ss3"]


def test_invalidate_subgraph_marks_seed_and_dependents() -> None:
    state = _tiny_project_state()
    marked = invalidate_subgraph(state, ["beat:b3"])
    assert marked == ["audio:ss3", "beat:b3", "shot:shot_b3", "utterance:ss3"]
    assert all(state.dependency_graph.nodes[ref].status == "stale" for ref in marked)
    assert state.dependency_graph.nodes["cast:host"].status == "current"


def test_invalidate_subgraph_aborts_when_a_dependent_is_locked() -> None:
    state = _tiny_project_state()
    state.dependency_graph.nodes["audio:ss3"].locked = True
    with pytest.raises(ValueError, match="locked"):
        invalidate_subgraph(state, ["beat:b3"])


def test_drive_repair_runs_frontiers_until_desired_are_current() -> None:
    state = _tiny_project_state()
    invalidated = invalidate_subgraph(state, ["beat:b3"])
    agents: list[str] = []

    def run_task(task: TaskEnvelope, instruction: str) -> None:
        agents.append(task.agent)
        assert instruction == "收成两句"
        _commit_task_targets(state, task)

    result = drive_repair(
        desired_refs=invalidated,
        instruction="收成两句",
        load_state=lambda: state,
        run_task=run_task,
    )
    assert result.ok is True
    assert result.complete is True
    assert agents[0] == "narrative_agent"
    assert "voice_agent" in agents
    assert all(
        state.dependency_graph.nodes[ref].status == "current" for ref in invalidated
    )


def test_drive_repair_replans_after_each_task_so_voice_sees_new_state_version() -> None:
    state = _tiny_project_state()
    invalidated = invalidate_subgraph(state, ["beat:b3"])
    seen_versions: list[int] = []

    def run_task(task: TaskEnvelope, instruction: str) -> None:
        del instruction
        assert task.based_on_state_version == state.context.state_version
        seen_versions.append(task.based_on_state_version)
        _commit_task_targets(state, task)
        state.context.state_version += 1

    result = drive_repair(
        desired_refs=invalidated,
        instruction="收成两句",
        load_state=lambda: state,
        run_task=run_task,
    )
    assert result.ok is True
    assert seen_versions == [2, 3]
    assert result.tasks_run[0].startswith("task_repair_narrative_agent_")
    assert result.tasks_run[1].startswith("task_repair_voice_agent_")


def test_editorial_run_does_not_add_legacy_graph_nodes(
    tmp_path: Path,
) -> None:
    state, _narrative, _voice, _plan = _complete_context(tmp_path)
    nodes = state.dependency_graph.nodes
    assert any(ref.startswith("beat:") for ref in nodes)
    assert any(ref.startswith("audio:") for ref in nodes)
    assert not any(ref.startswith("artifact:") for ref in nodes)
    assert not any(ref.startswith("visual_requirement:") for ref in nodes)
    assert not any(ref.startswith("realized_beat:") for ref in nodes)
    assert RepairScheduler().plan(state, _content_refs(state, "audio:")).complete is True

    audio_ref = _content_refs(state, "audio:")[0]
    state.dependency_graph.nodes[audio_ref].status = "stale"
    repair_plan = RepairScheduler().plan(state, [audio_ref])
    assert repair_plan.tasks[0].agent == "voice_agent"
    assert repair_plan.tasks[0].scope.target_refs == [audio_ref]
