"""Intake-owned identity writes. Harness only validates and commits."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..agents.narrative_agent.models import NarrativeArtifact
from ..harness.dependencies import DependencyGraph
from ..harness.dependency_builders import content_commits
from ..harness.description import (
    CastMember,
    VideoDescription,
    WorldPlace,
    WorldProp,
)
from ..harness.models import ProjectState
from ..harness.repair import (
    load_project_state,
    write_project_state,
)
from .description_view import get_element
from .graph_view import identity_refs_named, resolve_graph_ref
from .models import IntakeSession
from .view import format_progress, refresh_session_from_disk

_IDENTITY_PREFIXES = ("cast:", "prop:", "place:")
_ID_FIELDS = {
    "cast": "character_id",
    "prop": "prop_id",
    "place": "place_id",
}


def update_identity(
    session: IntakeSession,
    patches: list[dict[str, Any]] | None = None,
) -> tuple[IntakeSession, dict[str, Any]]:
    items = [item for item in (patches or []) if isinstance(item, dict)]
    if not items:
        return session, {"ok": False, "error": "patches 不能为空"}
    if not session.project_dir:
        return session, {"ok": False, "error": "还没有项目目录，无法改身份"}
    project_dir = Path(session.project_dir)
    state = load_project_state(project_dir)
    if state is None or state.production.structure is None:
        return session, {"ok": False, "error": "还没有 production"}
    try:
        changed = _apply_identity(state, items)
    except ValueError as exc:
        return session, {"ok": False, "error": str(exc)}
    write_project_state(project_dir, state)
    _sync_narrative_artifact_identity(project_dir, state)
    session = refresh_session_from_disk(session)
    return session, {
        "ok": True,
        "changed_refs": changed,
        "text": (
            f"已更新身份 {', '.join(changed)}。"
            f"{format_progress(session.progress)}"
        ),
        "progress": session.progress.model_dump(mode="json"),
    }


def _apply_identity(
    state: ProjectState,
    patches: list[dict[str, Any]],
) -> list[str]:
    changed: list[str] = []
    for item in patches:
        token = str(item.get("ref") or "").strip()
        targets = _resolve_identity_targets(state, token)
        if not targets:
            raise ValueError(f"图上没有身份 {token}")
        for ref in targets:
            _patch_world_node(state, ref, item)
            if ref not in changed:
                changed.append(ref)
    _commit_changed(state, changed)
    return changed


def _resolve_identity_targets(state: ProjectState, token: str) -> list[str]:
    if not token:
        return []
    resolved = resolve_graph_ref(state.dependency_graph.nodes, token)
    found: list[str] = []
    if resolved is not None and resolved.startswith(_IDENTITY_PREFIXES):
        found.append(_require_identity_ref(state, resolved))
        current = get_element(state.production, resolved)
        name = (current or {}).get("element", {}).get("name")
        if isinstance(name, str) and name.strip():
            for sibling in identity_refs_named(
                state.dependency_graph.nodes, state.production, name
            ):
                if sibling not in found:
                    found.append(_require_identity_ref(state, sibling))
        return found
    for ref in identity_refs_named(
        state.dependency_graph.nodes, state.production, token
    ):
        found.append(_require_identity_ref(state, ref))
    return found


def _require_identity_ref(state: ProjectState, ref: str) -> str:
    resolved = resolve_graph_ref(state.dependency_graph.nodes, ref)
    if resolved is None:
        raise ValueError(f"图上没有 {ref}")
    if not resolved.startswith(_IDENTITY_PREFIXES):
        raise ValueError("identity.update 只能改 cast:/prop:/place:")
    node = state.dependency_graph.nodes[resolved]
    if node.locked:
        raise ValueError(f"dependency node is locked: {resolved}")
    return resolved


def _patch_world_node(
    state: ProjectState, ref: str, patch: dict[str, Any]
) -> None:
    kind, _, ident = ref.partition(":")
    current = get_element(state.production, ref)
    if current is None or not isinstance(current.get("element"), dict):
        raise ValueError(f"production 里没有 {ref}")
    payload = dict(current["element"])
    id_field = _ID_FIELDS[kind]
    for key, value in patch.items():
        if key == "ref":
            continue
        if key == id_field and value != ident:
            raise ValueError(f"{ref} 不能改 {id_field}")
        payload[key] = value
    payload[id_field] = ident
    model = {"cast": CastMember, "prop": WorldProp, "place": WorldPlace}[kind]
    updated = model.model_validate(payload)
    _replace_world_item(state, kind, ident, updated)


def _replace_world_item(
    state: ProjectState, kind: str, ident: str, updated: object
) -> None:
    world = state.production.world
    if kind == "cast":
        world.cast = [
            updated if item.character_id == ident else item for item in world.cast
        ]
    elif kind == "prop":
        world.props = [
            updated if item.prop_id == ident else item for item in world.props
        ]
    else:
        world.places = [
            updated if item.place_id == ident else item for item in world.places
        ]


def _commit_changed(state: ProjectState, refs: list[str]) -> None:
    intent = state.creative.intent
    structure = state.production.structure
    if intent is None or structure is None:
        raise ValueError("production 不完整，无法提交身份")
    document = VideoDescription(
        intent=intent,
        world=state.production.world,
        structure=structure,
        voice=state.production.voice,
        shots=state.production.shots,
        timeline=state.production.timeline,
        deliverable=state.production.deliverable,
    )
    allowed = set(refs)
    commits = [item for item in content_commits(document) if item.ref in allowed]
    missing = allowed - {item.ref for item in commits}
    if missing:
        raise ValueError(f"无法提交这些身份节点：{sorted(missing)}")
    graph = DependencyGraph(state.dependency_graph)
    graph.commit_batch(
        task_id="identity_update",
        produced_by="narrative_agent",
        commits=commits,
    )
    state.context.state_version += 1


def _sync_narrative_artifact_identity(
    project_dir: Path, state: ProjectState
) -> None:
    path = project_dir / "narrative_artifact.json"
    if not path.is_file():
        return
    artifact = NarrativeArtifact.model_validate_json(path.read_text(encoding="utf-8"))
    names = {
        item.prop_id: item.name for item in state.production.world.props
    }
    names.update(
        {item.character_id: item.name for item in state.production.world.cast}
    )
    names.update(
        {item.place_id: item.name for item in state.production.world.places}
    )
    descriptions = {
        item.prop_id: item.description for item in state.production.world.props
    }
    descriptions.update(
        {item.character_id: item.description for item in state.production.world.cast}
    )
    descriptions.update(
        {item.place_id: item.description for item in state.production.world.places}
    )
    world_state = getattr(artifact.planning, "world_state", None)
    if world_state is not None:
        for entity in world_state.entities:
            if entity.entity_id in names:
                entity.name = names[entity.entity_id]
                entity.description = descriptions[entity.entity_id]
        materials = getattr(artifact.planning, "materials", None)
        if materials:
            for material in materials:
                if material.material_id in names:
                    material.name = names[material.material_id]
    path.write_text(artifact.model_dump_json(indent=2), encoding="utf-8")
