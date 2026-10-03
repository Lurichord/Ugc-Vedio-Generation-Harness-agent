"""Expand realization repair scope along identity and element edges."""

from __future__ import annotations

from .description_builder import refresh_shot_continuity
from .models import ProjectState

_IDENTITY_PREFIXES = ("cast:", "prop:", "place:")
_VOICE_PREFIXES = ("utterance:", "audio:", "script_segment:")
_KEEP_VOICE = ("只重配", "不改音色", "不改声")
_CHANGE_VOICE = ("换声", "音色", "男声", "女声", "沉一点", "整支")


def expand_asset_regenerate(
    state: ProjectState,
    graph_refs: list[str],
) -> tuple[list[str], list[str]]:
    """Return identity refs to redraw and shot ids to remake."""

    refresh_shot_continuity(state)
    identity_refs = [
        ref for ref in graph_refs if ref.startswith(_IDENTITY_PREFIXES)
    ]
    shot_ids = [
        ref.partition(":")[2] for ref in graph_refs if ref.startswith("shot:")
    ]
    nodes = state.dependency_graph.nodes
    for ident in identity_refs:
        node = nodes.get(ident)
        if node is None:
            continue
        for dep in node.dependents:
            if not dep.startswith("shot:"):
                continue
            shot_id = dep.partition(":")[2]
            if shot_id not in shot_ids and _shot_has_media(state, shot_id):
                shot_ids.append(shot_id)
    return identity_refs, _expand_continue_shots(state, shot_ids)


def expand_voice_refs(
    state: ProjectState,
    graph_refs: list[str],
    instruction: str,
) -> list[str]:
    """Return audio refs to resynthesize."""

    audio_refs = _collect_audio_refs(state, graph_refs)
    if not audio_refs:
        audio_refs = [ref for ref in graph_refs if ref.startswith("audio:")]
    if not _voice_expands_speaker(instruction, graph_refs):
        return audio_refs
    speakers = _speaker_refs(state, [*graph_refs, *audio_refs])
    if not speakers:
        return audio_refs
    found = list(audio_refs)
    for ref, node in state.dependency_graph.nodes.items():
        if not ref.startswith("audio:") or ref in found:
            continue
        if any(
            edge.family == "element" and edge.to in speakers for edge in node.edges
        ):
            found.append(ref)
    return found


def _voice_expands_speaker(instruction: str, graph_refs: list[str]) -> bool:
    if any(ref.startswith(_IDENTITY_PREFIXES) for ref in graph_refs):
        return True
    note = instruction.strip()
    if any(token in note for token in _KEEP_VOICE):
        return False
    return any(token in note for token in _CHANGE_VOICE)


def _speaker_refs(state: ProjectState, refs: list[str]) -> set[str]:
    speakers: set[str] = set()
    nodes = state.dependency_graph.nodes
    for ref in refs:
        if ref.startswith(_IDENTITY_PREFIXES):
            speakers.add(ref)
            continue
        node = nodes.get(ref)
        if node is None:
            continue
        for edge in node.edges:
            if edge.family == "element" and edge.to.startswith(_IDENTITY_PREFIXES):
                speakers.add(edge.to)
    return speakers


def _expand_continue_shots(state: ProjectState, shot_ids: list[str]) -> list[str]:
    ordered = list(dict.fromkeys(shot_ids))
    seen = set(ordered)
    nodes = state.dependency_graph.nodes
    queue = [f"shot:{shot_id}" for shot_id in ordered]
    while queue:
        current = queue.pop(0)
        node = nodes.get(current)
        if node is not None:
            for dep in node.dependents:
                if not dep.startswith("shot:"):
                    continue
                child = nodes.get(dep)
                if child is None:
                    continue
                if not any(
                    edge.family == "element" and edge.to == current
                    for edge in child.edges
                ):
                    continue
                shot_id = dep.partition(":")[2]
                child_shot = _shot_item(state, shot_id)
                if (
                    child_shot is None
                    or not child_shot.continuity.uses_previous_tail()
                    or child_shot.continuity.previous_shot_id
                    != current.partition(":")[2]
                ):
                    continue
                if shot_id in seen:
                    continue
                seen.add(shot_id)
                ordered.append(shot_id)
                queue.append(dep)
        current_id = current.partition(":")[2]
        for shot in state.production.shots:
            if not shot.continuity.uses_previous_tail():
                continue
            if shot.continuity.previous_shot_id != current_id:
                continue
            if shot.spec.shot_id in seen:
                continue
            seen.add(shot.spec.shot_id)
            ordered.append(shot.spec.shot_id)
            queue.append(f"shot:{shot.spec.shot_id}")
    return ordered


def _shot_item(state: ProjectState, shot_id: str):
    for item in state.production.shots:
        if item.spec.shot_id == shot_id:
            return item
    return None


def _shot_has_media(state: ProjectState, shot_id: str) -> bool:
    for item in state.production.shots:
        if item.spec.shot_id != shot_id:
            continue
        media = item.media
        return media is not None and bool(media.local_path)
    return False


def _collect_audio_refs(state: ProjectState, graph_refs: list[str]) -> list[str]:
    found: list[str] = []
    nodes = state.dependency_graph.nodes
    for ref in graph_refs:
        if ref.startswith("audio:") and ref not in found:
            found.append(ref)
            continue
        node = nodes.get(ref)
        if node is None:
            continue
        for dep in node.dependents:
            if dep.startswith("audio:") and dep not in found:
                found.append(dep)
        for other in nodes.values():
            if other.ref.startswith("audio:") and ref in other.depends_on:
                if other.ref not in found:
                    found.append(other.ref)
    return found
