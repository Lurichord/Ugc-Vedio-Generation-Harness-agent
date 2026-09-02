"""Slim dependency-graph views for the intent layer. Never dump hashes or all shots."""

from __future__ import annotations

from typing import Any

from ..harness.dependency_builders import CONTENT_ROOT_PREFIXES
from ..harness.models import DependencyGraphState, DependencyNode, ProjectState
from .description_view import get_element

_DESCRIPTION_ALIASES: dict[str, tuple[str, ...]] = {
    "beat": ("planned_beat", "realized_beat"),
    "planned_beat": ("beat",),
    "realized_beat": ("beat",),
    "section": ("section",),
    "scene": ("scene",),
    "step": ("step",),
    "action": ("action",),
    "shot": ("shot",),
    "clip": ("timeline_clip",),
    "timeline_clip": ("clip",),
    "cast": ("character",),
    "character": ("cast",),
    "prop": ("prop",),
    "place": ("place",),
    "utterance": ("script_segment",),
    "script_segment": ("utterance",),
    "audio": ("audio_segment", "voice_segment"),
    "audio_segment": ("audio",),
    "voice_segment": ("audio",),
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


def identity_refs_named(
    nodes: dict[str, DependencyNode],
    production: Any | None,
    name: str,
) -> list[str]:
    if production is None:
        return []
    wanted = name.strip()
    if not wanted:
        return []
    world = getattr(production, "world", None)
    if world is None:
        return []
    candidates = [
        *(
            f"cast:{item.character_id}"
            for item in getattr(world, "cast", [])
            if _item_name(item) == wanted
        ),
        *(
            f"prop:{item.prop_id}"
            for item in getattr(world, "props", [])
            if _item_name(item) == wanted
        ),
        *(
            f"place:{item.place_id}"
            for item in getattr(world, "places", [])
            if _item_name(item) == wanted
        ),
    ]
    return [ref for ref in candidates if ref in nodes]


def _item_name(item: Any) -> str:
    value = getattr(item, "name", None)
    return value.strip() if isinstance(value, str) else ""


_IDENTITY_PREFIXES = ("cast:", "prop:", "place:")


def dump_node(
    node: DependencyNode,
    *,
    edge_refs: set[str] | None = None,
    production: Any | None = None,
) -> dict[str, Any]:
    depends_on = list(node.depends_on)
    dependents = list(node.dependents)
    if edge_refs is not None:
        depends_on = [item for item in depends_on if item in edge_refs]
        dependents = [item for item in dependents if item in edge_refs]
    payload = {
        "ref": node.ref,
        "kind": node.kind,
        "produced_by": node.produced_by,
        "status": node.status,
        "locked": node.locked,
        "depends_on": depends_on,
        "dependents": dependents,
    }
    if node.edges:
        payload["edges"] = [
            {"to": edge.to, "family": edge.family}
            for edge in node.edges
            if edge_refs is None or edge.to in edge_refs
        ]
    name = _identity_name(production, node.ref)
    if name:
        payload["name"] = name
    return payload


def list_graph(
    state: ProjectState | DependencyGraphState | None,
    around_ref: str | None = None,
) -> dict[str, Any]:
    graph = _graph_state(state)
    if graph is None:
        return {"ok": False, "error": "找不到 dependency_graph"}
    nodes = graph.nodes
    production = _production(state)
    focus = (around_ref or "").strip() or None
    if focus is None:
        return _content_root_overview(nodes, production)
    return _neighborhood(nodes, focus, production)


def _production(state: ProjectState | DependencyGraphState | None) -> Any | None:
    if isinstance(state, ProjectState):
        return state.production
    return None


def _identity_name(production: Any | None, ref: str) -> str | None:
    if production is None or not ref.startswith(_IDENTITY_PREFIXES):
        return None
    current = get_element(production, ref)
    element = (current or {}).get("element")
    if not isinstance(element, dict):
        return None
    name = element.get("name")
    if isinstance(name, str) and name.strip():
        return name
    return None


def _graph_state(
    state: ProjectState | DependencyGraphState | None,
) -> DependencyGraphState | None:
    if state is None:
        return None
    if isinstance(state, DependencyGraphState):
        return state
    return state.dependency_graph


def _content_root_overview(
    nodes: dict[str, DependencyNode],
    production: Any | None,
) -> dict[str, Any]:
    root_refs = {ref for ref in nodes if ref.startswith(CONTENT_ROOT_PREFIXES)}
    dumped = []
    for ref in sorted(root_refs):
        if ref.startswith(_IDENTITY_PREFIXES):
            dumped.append(dump_node(nodes[ref], production=production))
        else:
            dumped.append(
                dump_node(
                    nodes[ref],
                    edge_refs=root_refs,
                    production=production,
                )
            )
    return {
        "ok": True,
        "scope": "roots",
        "nodes": dumped,
    }


def _neighborhood(
    nodes: dict[str, DependencyNode],
    around_ref: str,
    production: Any | None,
) -> dict[str, Any]:
    resolved = resolve_graph_ref(nodes, around_ref)
    centers: list[str]
    if resolved is not None:
        centers = [resolved]
    else:
        centers = identity_refs_named(nodes, production, around_ref)
        if not centers:
            tried = candidate_graph_refs(around_ref)
            return {
                "ok": False,
                "error": f"图上没有 {around_ref}",
                "tried": tried,
            }
        resolved = centers[0]
    neighbor_refs: set[str] = set()
    for ref in centers:
        center = nodes[ref]
        neighbor_refs.update(center.depends_on)
        neighbor_refs.update(center.dependents)
    selected = list(dict.fromkeys([*centers, *sorted(neighbor_refs)]))
    dumped = [
        dump_node(nodes[ref], edge_refs=set(selected), production=production)
        for ref in selected
        if ref in nodes
    ]
    payload: dict[str, Any] = {
        "ok": True,
        "scope": "neighborhood",
        "around_ref": around_ref,
        "resolved_ref": resolved,
        "nodes": dumped,
    }
    if around_ref != resolved:
        payload["resolved_from"] = around_ref
    if len(centers) > 1:
        payload["resolved_refs"] = centers
    return payload
