"""Read and write identity roster entries on ProjectState.production.world."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .description import VideoDescription
from .models import ProjectState

_IDENTITY_KINDS = ("cast", "prop", "place")


def document_from_state(state: ProjectState) -> VideoDescription:
    intent = state.creative.intent
    structure = state.production.structure
    if intent is None or structure is None:
        raise ValueError("production 不完整")
    return VideoDescription(
        intent=intent,
        world=state.production.world,
        structure=structure,
        voice=state.production.voice,
        shots=state.production.shots,
        timeline=state.production.timeline,
        deliverable=state.production.deliverable,
    )


def bound_identity_refs(state: ProjectState) -> list[str]:
    """Identities that at least one shot binds, in roster order."""

    bound: set[str] = set()
    for shot in state.production.shots:
        bound.update(shot.spec.binds)
    for node in state.dependency_graph.nodes.values():
        if node.kind in _IDENTITY_KINDS and any(
            item.startswith("shot:") for item in node.dependents
        ):
            bound.add(node.ref)
    ordered: list[str] = []
    world = state.production.world
    for member in world.cast:
        ref = f"cast:{member.character_id}"
        if ref in bound and ref not in ordered:
            ordered.append(ref)
    for prop in world.props:
        ref = f"prop:{prop.prop_id}"
        if ref in bound and ref not in ordered:
            ordered.append(ref)
    for place in world.places:
        ref = f"place:{place.place_id}"
        if ref in bound and ref not in ordered:
            ordered.append(ref)
    for ref in sorted(bound):
        if ref not in ordered:
            ordered.append(ref)
    return ordered


def identity_record(state: ProjectState, ref: str) -> Any | None:
    kind, _, ident = ref.partition(":")
    world = state.production.world
    if kind == "cast":
        items, key = world.cast, "character_id"
    elif kind == "prop":
        items, key = world.props, "prop_id"
    elif kind == "place":
        items, key = world.places, "place_id"
    else:
        return None
    for item in items:
        if getattr(item, key) == ident:
            return item
    return None


def identity_prompt(item: Any) -> str:
    name = str(getattr(item, "name", "") or "subject")
    description = str(getattr(item, "description", "") or "").strip()
    appearance = getattr(item, "appearance", None)
    if appearance is None:
        constraints = getattr(item, "appearance_constraints", None) or []
        appearance = "、".join(str(part) for part in constraints if part)
    else:
        appearance = str(appearance).strip()
    features = "、".join(part for part in (description, appearance) if part)
    labeled = f"{name}：{features}" if features else name
    if getattr(item, "character_id", None):
        return (
            f"9:16 竖屏锁脸静帧。{labeled}。"
            "无表情，面部清楚，肩部以上占比大，浅色虚化背景。"
            "不要全身远景，不要三视图，无文字无 logo。"
        )
    if getattr(item, "place_id", None):
        return (
            f"9:16 竖屏场景静帧。{labeled}。"
            "主体占画面主要位置。无文字无 logo。"
        )
    return (
        f"9:16 竖屏道具静帧。{labeled}。"
        "主体大、居中，形状材料颜色清楚。无文字无 logo。"
    )


def set_reference_image(state: ProjectState, ref: str, relative_path: str) -> None:
    item = identity_record(state, ref)
    if item is None:
        raise ValueError(f"production 里没有身份 {ref}")
    item.reference_image = relative_path


def identity_image_path(project_dir: Path, ref: str) -> Path:
    kind, _, ident = ref.partition(":")
    safe = ident.replace("/", "_")
    return Path(project_dir) / "assets" / "identity_reference" / f"{kind}_{safe}.png"


def missing_identity_images(state: ProjectState) -> bool:
    for ref in bound_identity_refs(state):
        item = identity_record(state, ref)
        if item is None or not getattr(item, "reference_image", None):
            return True
    return False


def readable_reference(project_dir: Path, relative: str | None) -> bool:
    if not relative:
        return False
    path = Path(project_dir) / relative
    return path.is_file() and path.stat().st_size > 0
