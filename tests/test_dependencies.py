from ugc_harness.harness.dependencies import DependencyGraph, NodeCommit
from ugc_harness.harness.models import DependencyEdge, DependencyGraphState
import pytest


def test_dependency_graph_keeps_bidirectional_beat_edges() -> None:
    state = DependencyGraphState()
    graph = DependencyGraph(state)

    graph.commit_batch(
        task_id="task_narrative_1",
        produced_by="narrative_agent",
        commits=[
            NodeCommit("beat:pb01", "beat", {"text": "beat"}),
            NodeCommit(
                "utterance:ss01",
                "utterance",
                {"text": "script"},
                ("beat:pb01",),
            ),
        ],
    )

    beat = state.nodes["beat:pb01"]
    script = state.nodes["utterance:ss01"]
    assert script.depends_on == ["beat:pb01"]
    assert beat.dependents == ["utterance:ss01"]
    assert script.dependency_versions["beat:pb01"] == beat.version
    assert script.dependency_hashes == {}


def test_changed_beat_invalidates_only_its_descendants() -> None:
    state = DependencyGraphState()
    graph = DependencyGraph(state)
    graph.commit_batch(
        task_id="task_initial",
        produced_by="test",
        commits=[
            NodeCommit("beat:pb01", "beat", "beat one"),
            NodeCommit("beat:pb02", "beat", "beat two"),
            NodeCommit(
                "utterance:ss01",
                "utterance",
                "script one",
                ("beat:pb01",),
            ),
            NodeCommit(
                "utterance:ss02",
                "utterance",
                "script two",
                ("beat:pb02",),
            ),
            NodeCommit(
                "audio:ss01",
                "audio",
                "audio one",
                ("utterance:ss01",),
            ),
            NodeCommit(
                "audio:ss02",
                "audio",
                "audio two",
                ("utterance:ss02",),
            ),
        ],
    )

    update = graph.commit_batch(
        task_id="task_revise_pb01",
        produced_by="narrative_agent",
        commits=[
            NodeCommit("beat:pb01", "beat", "changed beat one")
        ],
    )

    assert update.invalidated_refs == [
        "audio:ss01",
        "utterance:ss01",
    ]
    assert state.nodes["audio:ss01"].status == "stale"
    assert state.nodes["audio:ss02"].status == "current"
    assert state.nodes["utterance:ss02"].status == "current"


def test_rejected_task_advances_graph_audit_without_committing_nodes() -> None:
    state = DependencyGraphState()
    update = DependencyGraph(state).reject_update(
        task_id="task_editorial_revision",
        candidate_refs=["shot:shot_pb04"],
        reason="critic rejected candidate",
    )

    assert state.graph_version == 1
    assert update.committed is False
    assert update.rejected_refs == ["shot:shot_pb04"]
    assert state.nodes == {}


def test_invalid_graph_commit_is_atomic() -> None:
    state = DependencyGraphState()
    graph = DependencyGraph(state)
    graph.commit_batch(
        task_id="task_initial",
        produced_by="test",
        commits=[NodeCommit("beat:pb01", "beat", "initial")],
    )
    before = state.model_copy(deep=True)

    with pytest.raises(ValueError, match="cycle"):
        graph.commit_batch(
            task_id="task_cycle",
            produced_by="test",
            commits=[
                NodeCommit(
                    "beat:pb01",
                    "beat",
                    "changed",
                    ("utterance:ss01",),
                ),
                NodeCommit(
                    "utterance:ss01",
                    "utterance",
                    "script",
                    ("beat:pb01",),
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
        commits=[NodeCommit("beat:pb01", "beat", "initial")],
    )
    snapshot = graph.snapshot(["beat:pb01"])
    graph.commit_batch(
        task_id="task_revision",
        produced_by="narrative_agent",
        commits=[NodeCommit("beat:pb01", "beat", "changed")],
    )

    with pytest.raises(ValueError, match="STALE_RESULT"):
        graph.validate_snapshot(snapshot)


def test_locked_node_cannot_be_overwritten_and_graph_rolls_back() -> None:
    state = DependencyGraphState()
    graph = DependencyGraph(state)
    graph.commit_batch(
        task_id="task_initial",
        produced_by="narrative_agent",
        commits=[NodeCommit("utterance:ss01", "utterance", "locked")],
    )
    state.nodes["utterance:ss01"].locked = True
    before = state.model_copy(deep=True)

    with pytest.raises(ValueError, match="locked"):
        graph.commit_batch(
            task_id="task_overwrite",
            produced_by="narrative_agent",
            commits=[
                NodeCommit("utterance:ss01", "utterance", "overwrite")
            ],
        )

    assert state == before


def test_identical_rewrite_still_invalidates_descendants() -> None:
    state = DependencyGraphState()
    graph = DependencyGraph(state)
    graph.commit_batch(
        task_id="task_initial",
        produced_by="test",
        commits=[
            NodeCommit("beat:pb01", "beat", "same beat"),
            NodeCommit(
                "utterance:ss01",
                "utterance",
                "script",
                ("beat:pb01",),
            ),
        ],
    )
    snapshot = graph.snapshot(["beat:pb01"])
    assert state.nodes["beat:pb01"].version == 1

    update = graph.commit_batch(
        task_id="task_rewrite",
        produced_by="test",
        commits=[NodeCommit("beat:pb01", "beat", "same beat")],
    )

    assert update.changed_refs == ["beat:pb01"]
    assert update.refreshed_refs == []
    assert update.invalidated_refs == ["utterance:ss01"]
    assert state.nodes["beat:pb01"].status == "current"
    assert state.nodes["beat:pb01"].version == 2
    assert state.nodes["utterance:ss01"].status == "stale"
    with pytest.raises(ValueError, match="STALE_RESULT"):
        graph.validate_snapshot(snapshot)


def test_changing_cast_dirties_bound_shot_and_audio() -> None:
    state = DependencyGraphState()
    graph = DependencyGraph(state)
    graph.commit_batch(
        task_id="task_initial",
        produced_by="test",
        commits=[
            NodeCommit("cast:host", "cast", "host"),
            NodeCommit("beat:pb01", "beat", "beat"),
            NodeCommit(
                "utterance:ss01",
                "utterance",
                "line",
                edges=(DependencyEdge(to="beat:pb01", family="plot"),),
            ),
            NodeCommit(
                "audio:ss01",
                "audio",
                "wav",
                edges=(
                    DependencyEdge(to="utterance:ss01", family="element"),
                    DependencyEdge(to="cast:host", family="element"),
                ),
            ),
            NodeCommit(
                "shot:shot_pb01",
                "shot",
                "shot",
                edges=(
                    DependencyEdge(to="beat:pb01", family="plot"),
                    DependencyEdge(to="cast:host", family="identity"),
                ),
            ),
        ],
    )

    update = graph.commit_batch(
        task_id="task_change_cast",
        produced_by="test",
        commits=[NodeCommit("cast:host", "cast", "new host")],
    )

    assert set(update.invalidated_refs) == {"audio:ss01", "shot:shot_pb01"}
    assert state.nodes["utterance:ss01"].status == "current"
