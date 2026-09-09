from pathlib import Path
from types import SimpleNamespace

from app.backend.graph_history import (
    install_hooks,
    load_history_index,
    load_history_snapshot,
    record_graph_snapshot,
)
from app.backend.graph_projector import GraphProjector
from ugc_harness.harness.dependencies import DependencyGraph, NodeCommit
from ugc_harness.harness.models import DependencyGraphState, ProjectState
from ugc_harness.harness.repair import load_project_state, write_project_state


def _state_with_graph() -> ProjectState:
    graph = DependencyGraph(DependencyGraphState())
    graph.commit_batch(
        task_id="seed",
        produced_by="narrative_agent",
        commits=[NodeCommit("prop:p1", "prop", "flour")],
    )
    return ProjectState(dependency_graph=graph.state)


def test_write_project_state_does_not_record_without_backend_hook(tmp_path: Path) -> None:
    from ugc_harness.harness import repair
    from ugc_harness.shared import artifacts

    previous = (
        repair.on_project_state_written,
        artifacts.on_project_state_written,
    )
    repair.on_project_state_written = None
    artifacts.on_project_state_written = None
    try:
        project_dir = tmp_path / "demo"
        write_project_state(project_dir, _state_with_graph())
        assert load_history_index(project_dir)["versions"] == []
    finally:
        repair.on_project_state_written, artifacts.on_project_state_written = previous


def test_write_project_state_records_a_snapshot(tmp_path: Path) -> None:
    install_hooks()
    project_dir = tmp_path / "demo"
    state = _state_with_graph()
    write_project_state(project_dir, state)
    index = load_history_index(project_dir)
    assert [item["version"] for item in index["versions"]] == [1]
    snapshot = load_history_snapshot(project_dir, 1)
    assert "prop:p1" in snapshot["dependency_graph"]["nodes"]
    write_project_state(project_dir, state)
    assert [item["version"] for item in load_history_index(project_dir)["versions"]] == [1]


def test_graph_change_adds_the_next_version(tmp_path: Path) -> None:
    install_hooks()
    project_dir = tmp_path / "demo"
    state = _state_with_graph()
    write_project_state(project_dir, state)
    DependencyGraph(state.dependency_graph).commit_batch(
        task_id="add",
        produced_by="narrative_agent",
        commits=[NodeCommit("place:x1", "place", "kitchen")],
    )
    write_project_state(project_dir, state)
    versions = load_history_index(project_dir)["versions"]
    assert [item["version"] for item in versions] == [1, 2]
    first = load_history_snapshot(project_dir, 1)
    second = load_history_snapshot(project_dir, 2)
    assert "place:x1" not in first["dependency_graph"]["nodes"]
    assert "place:x1" in second["dependency_graph"]["nodes"]


def test_projector_can_render_a_past_graph_version(tmp_path: Path) -> None:
    install_hooks()
    project_dir = tmp_path / "demo"
    state = _state_with_graph()
    write_project_state(project_dir, state)
    DependencyGraph(state.dependency_graph).commit_batch(
        task_id="add",
        produced_by="narrative_agent",
        commits=[NodeCommit("place:x1", "place", "kitchen")],
    )
    write_project_state(project_dir, state)
    projects = SimpleNamespace(read_state=lambda _path: load_project_state(project_dir))
    past = GraphProjector(projects).view(project_dir, version=1)
    live = GraphProjector(projects).view(project_dir)
    assert past["history_version"] == 1
    assert live["history_version"] is None
    assert [item["version"] for item in live["history"]] == [1, 2]
    assert "place:x1" not in {node["ref"] for node in past["nodes"]}
    assert "place:x1" in {node["ref"] for node in live["nodes"]}


def test_empty_graph_is_not_snapshotted(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    write_project_state(project_dir, ProjectState())
    assert record_graph_snapshot(project_dir, ProjectState()) is None
    assert load_history_index(project_dir)["versions"] == []
