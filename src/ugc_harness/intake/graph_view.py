"""Slim dependency-graph views for the intent layer. Never dump hashes or all shots."""

from __future__ import annotations

from typing import Any

from ..harness.models import DependencyGraphState, DependencyNode, ProjectState

_DESCRIPTION_ALIASES: dict[str, tuple[str, ...]] = {
    "beat": ("planned_beat", "realized_beat"),
    "shot": ("shot", "timeline_clip"),
    "clip": ("timeline_clip",),
}


def candidate_graph_refs(ref: str) -> list[str]:
    cleaned = ref.strip()
    if not cleaned:
        return []
    names = [cleaned]
    kind, _, ident = cleaned.partition(":")
    if not ident:
        return names
    for prefix in _DESCRIPTION_ALIASES.get(kind, ()):
        alias = f"{prefix}:{ident}"
        if alias not in names:
            names.append(alias)
    return names


def resolve_graph_ref(nodes: dict[str, DependencyNode], ref: str) -> str | None:
    for candidate in candidate_graph_refs(ref):
        if candidate in nodes:
            return candidate
    return None


def dump_node(
    node: DependencyNode,
    *,
    edge_refs: set[str] | None = None,
) -> dict[str, Any]:
    depends_on = list(node.depends_on)
    dependents = list(node.dependents)
    if edge_refs is not None:
        depends_on = [item for item in depends_on if item in edge_refs]
        dependents = [item for item in dependents if item in edge_refs]
    return {
        "ref": node.ref,
        "kind": node.kind,
        "produced_by": node.produced_by,
        "status": node.status,
        "locked": node.locked,
        "depends_on": depends_on,
        "dependents": dependents,
    }


def list_graph(
    state: ProjectState | DependencyGraphState | None,
    around_ref: str | None = None,
) -> dict[str, Any]:
    graph = _graph_state(state)
    if graph is None:
        return {"ok": False, "error": "找不到 dependency_graph"}
    nodes = graph.nodes
    focus = (around_ref or "").strip() or None
    if focus is None:
        return _artifact_overview(nodes)
    return _neighborhood(nodes, focus)


def _graph_state(
    state: ProjectState | DependencyGraphState | None,
) -> DependencyGraphState | None:
    if state is None:
        return None
    if isinstance(state, DependencyGraphState):
        return state
    return state.dependency_graph


def _artifact_overview(nodes: dict[str, DependencyNode]) -> dict[str, Any]:
    artifact_refs = {ref for ref in nodes if ref.startswith("artifact:")}
    dumped = [
        dump_node(nodes[ref], edge_refs=artifact_refs)
        for ref in sorted(artifact_refs)
    ]
    return {
        "ok": True,
        "scope": "artifacts",
        "nodes": dumped,
    }


def _neighborhood(nodes: dict[str, DependencyNode], around_ref: str) -> dict[str, Any]:
    resolved = resolve_graph_ref(nodes, around_ref)
    if resolved is None:
        tried = candidate_graph_refs(around_ref)
        return {
            "ok": False,
            "error": f"图上没有 {around_ref}",
            "tried": tried,
        }
    center = nodes[resolved]
    neighbor_refs = set(center.depends_on) | set(center.dependents)
    if resolved.startswith("artifact:"):
        shown_neighbors = {ref for ref in neighbor_refs if ref.startswith("artifact:")}
    else:
        shown_neighbors = neighbor_refs
    omitted_edges = len(neighbor_refs - shown_neighbors)
    selected = [resolved, *sorted(shown_neighbors)]
    dumped = [dump_node(nodes[ref], edge_refs=set(selected)) for ref in selected]
    payload: dict[str, Any] = {
        "ok": True,
        "scope": "neighborhood",
        "around_ref": around_ref,
        "resolved_ref": resolved,
        "nodes": dumped,
    }
    if around_ref != resolved:
        payload["resolved_from"] = around_ref
    if omitted_edges:
        payload["omitted_edges"] = omitted_edges
    return payload
