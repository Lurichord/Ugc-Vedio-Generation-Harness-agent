from ugc_harness.harness.dependencies import DependencyGraph, NodeCommit
from ugc_harness.harness.models import DependencyGraphState
import pytest


def test_dependency_graph_keeps_bidirectional_beat_edges() -> None:
    state = DependencyGraphState()
    graph = DependencyGraph(state)

    graph.commit_batch(
        task_id="task_narrative_1",
        produced_by="narrative_agent",
        commits=[
            NodeCommit("planned_beat:pb01", "planned_beat", {"text": "beat"}),
            NodeCommit(
                "script_segment:ss01",
                "script_segment",
                {"text": "script"},
                ("planned_beat:pb01",),
            ),
        ],
    )

    beat = state.nodes["planned_beat:pb01"]
    script = state.nodes["script_segment:ss01"]
    assert script.depends_on == ["planned_beat:pb01"]
    assert beat.dependents == ["script_segment:ss01"]
    assert script.dependency_versions["planned_beat:pb01"] == beat.version
    assert script.dependency_hashes["planned_beat:pb01"] == beat.content_hash


def test_changed_beat_invalidates_only_its_descendants() -> None:
    state = DependencyGraphState()
    graph = DependencyGraph(state)
    graph.commit_batch(
        task_id="task_initial",
        produced_by="test",
        commits=[
            NodeCommit("planned_beat:pb01", "planned_beat", "beat one"),
            NodeCommit("planned_beat:pb02", "planned_beat", "beat two"),
            NodeCommit(
                "script_segment:ss01",
                "script_segment",
                "script one",
                ("planned_beat:pb01",),
            ),
            NodeCommit(
                "script_segment:ss02",
                "script_segment",
                "script two",
                ("planned_beat:pb02",),
            ),
            NodeCommit(
                "audio_segment:as01",
                "audio_segment",
                "audio one",
                ("script_segment:ss01",),
            ),
            NodeCommit(
                "audio_segment:as02",
                "audio_segment",
                "audio two",
                ("script_segment:ss02",),
            ),
        ],
    )

    update = graph.commit_batch(
        task_id="task_revise_pb01",
        produced_by="narrative_agent",
        commits=[
            NodeCommit("planned_beat:pb01", "planned_beat", "changed beat one")
        ],
    )

    assert update.invalidated_refs == [
        "audio_segment:as01",
        "script_segment:ss01",
    ]
    assert state.nodes["audio_segment:as01"].status == "stale"
    assert state.nodes["audio_segment:as02"].status == "current"
    assert state.nodes["script_segment:ss02"].status == "current"


def test_rejected_task_advances_graph_audit_without_committing_nodes() -> None:
    state = DependencyGraphState()
    update = DependencyGraph(state).reject_update(
        task_id="task_editorial_revision",
        candidate_refs=["visual_requirement:vr04"],
        reason="critic rejected candidate",
    )

    assert state.graph_version == 1
    assert update.committed is False
    assert update.rejected_refs == ["visual_requirement:vr04"]
    assert state.nodes == {}


def test_invalid_graph_commit_is_atomic() -> None:
    state = DependencyGraphState()
    graph = DependencyGraph(state)
    graph.commit_batch(
        task_id="task_initial",
        produced_by="test",
        commits=[NodeCommit("planned_beat:pb01", "planned_beat", "initial")],
    )
    before = state.model_copy(deep=True)

    with pytest.raises(ValueError, match="cycle"):
        graph.commit_batch(
            task_id="task_cycle",
            produced_by="test",
            commits=[
                NodeCommit(
                    "planned_beat:pb01",
                    "planned_beat",
                    "changed",
                    ("script_segment:ss01",),
                ),
                NodeCommit(
                    "script_segment:ss01",
                    "script_segment",
                    "script",
                    ("planned_beat:pb01",),
                ),
            ],
        )

    assert state == before


def test_dependency_snapshot_rejects_result_after_input_changes() -> None:
    state = DependencyGraphState()
    graph = DependencyGraph(state)
    graph.commit_batch(
        task_id="task_initial",
        produced_by="narrative_agent",
        commits=[NodeCommit("planned_beat:pb01", "planned_beat", "initial")],
    )
    snapshot = graph.snapshot(["planned_beat:pb01"])
    graph.commit_batch(
        task_id="task_revision",
        produced_by="narrative_agent",
        commits=[NodeCommit("planned_beat:pb01", "planned_beat", "changed")],
    )

    with pytest.raises(ValueError, match="STALE_RESULT"):
        graph.validate_snapshot(snapshot)


def test_locked_node_cannot_be_overwritten_and_graph_rolls_back() -> None:
    state = DependencyGraphState()
    graph = DependencyGraph(state)
    graph.commit_batch(
        task_id="task_initial",
        produced_by="narrative_agent",
        commits=[NodeCommit("script_segment:ss01", "script_segment", "locked")],
    )
    state.nodes["script_segment:ss01"].locked = True
    before = state.model_copy(deep=True)

    with pytest.raises(ValueError, match="locked"):
        graph.commit_batch(
            task_id="task_overwrite",
            produced_by="narrative_agent",
            commits=[
                NodeCommit("script_segment:ss01", "script_segment", "overwrite")
            ],
        )

    assert state == before
