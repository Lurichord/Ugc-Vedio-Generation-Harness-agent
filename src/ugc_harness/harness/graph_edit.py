"""Add, bind, and remove identity / shot nodes. Edges stay derived from production."""

from __future__ import annotations

from typing import Any

from ..content import DramaAction, ProductionShot, TutorialAction, TutorialMaterial
from .dependencies import DependencyGraph
from .dependency_builders import content_commits
from .description import (
    CastMember,
    DramaStructure,
    ShotContinuity,
    ShotDescription,
    TutorialStructure,
    VideoDescription,
    WorldPlace,
    WorldProp,
)
from .models import (
    ElementStatus,
    ProjectState,
    ShotCreateSpec,
    TaskBudget,
    TaskEnvelope,
    TaskScope,
)
from .narrative_formats import REPAIR_SUBMIT_TOOL
from .repair import repair_input_hash

_IDENTITY_PREFIXES = ("cast:", "prop:", "place:")

_ANCHOR_FOR_KIND = {
    "drama": "scene:",
    "tutorial": "step:",
    "explainer": "beat:",
}
_ID_PREFIX = {"cast": "cast", "prop": "prop", "place": "place"}
_ID_ATTR = {"cast": "character_id", "prop": "prop_id", "place": "place_id"}


def narrative_format(state: ProjectState) -> str:
    raw = state.context.constraints.get(
        "narrative_format"
    ) or state.context.constraints.get("production_mode")
    if raw in {"drama", "tutorial", "explainer"}:
        return str(raw)
    structure = state.production.structure
    if structure is not None:
        return structure.kind
    return "explainer"


def resolve_identity_token(state: ProjectState, token: str) -> list[str]:
    cleaned = token.strip()
    if not cleaned:
        return []
    nodes = state.dependency_graph.nodes
    if cleaned in nodes and cleaned.startswith(_IDENTITY_PREFIXES):
        return [cleaned]
    kind, _, ident = cleaned.partition(":")
    if ident and f"{kind}:{ident}" in nodes and cleaned.startswith(_IDENTITY_PREFIXES):
        return [cleaned]
    found: list[str] = []
    world = state.production.world
    for item in world.cast:
        if item.name == cleaned:
            ref = f"cast:{item.character_id}"
            if ref in nodes:
                found.append(ref)
    for item in world.props:
        if item.name == cleaned:
            ref = f"prop:{item.prop_id}"
            if ref in nodes:
                found.append(ref)
    for item in world.places:
        if item.name == cleaned:
            ref = f"place:{item.place_id}"
            if ref in nodes:
                found.append(ref)
    return list(dict.fromkeys(found))


def next_identity_id(state: ProjectState, kind: str) -> str:
    prefix = _ID_PREFIX[kind]
    existing = _identity_ids(state, kind)
    index = 1
    while f"{prefix}_{index}" in existing:
        index += 1
    return f"{prefix}_{index}"


def add_identity(
    state: ProjectState,
    *,
    kind: str,
    name: str,
    description: str,
    appearance: str | None = None,
    role: str = "performer",
) -> str:
    if kind not in _ID_PREFIX:
        raise ValueError("identity.create 只能加 cast / prop / place")
    cleaned_name = name.strip()
    cleaned_description = description.strip()
    if not cleaned_name:
        raise ValueError("name 不能为空")
    if not cleaned_description:
        raise ValueError("description 不能为空")
    ident = next_identity_id(state, kind)
    world = state.production.world
    if kind == "cast":
        if role not in {"narrator", "performer"}:
            raise ValueError("cast role 只能是 narrator 或 performer")
        world.cast.append(
            CastMember(
                character_id=ident,
                name=cleaned_name,
                role=role,  # type: ignore[arg-type]
                description=cleaned_description,
                appearance_constraints=(
                    [appearance.strip()] if appearance and appearance.strip() else []
                ),
            )
        )
    elif kind == "prop":
        world.props.append(
            WorldProp(
                prop_id=ident,
                name=cleaned_name,
                description=cleaned_description,
                appearance=appearance.strip() if appearance and appearance.strip() else None,
            )
        )
        _append_tutorial_material(state, ident, cleaned_name)
    else:
        world.places.append(
            WorldPlace(
                place_id=ident,
                name=cleaned_name,
                description=cleaned_description,
                appearance=appearance.strip() if appearance and appearance.strip() else None,
            )
        )
    ref = f"{kind}:{ident}"
    _commit_refs(state, [ref], task_id="identity_create")
    state.evaluation.elements.setdefault(ref, ElementStatus(status="passed", version=1))
    return ref


def bind_identity(
    state: ProjectState,
    shot_refs: list[str],
    identity_ref: str,
) -> list[str]:
    targets = resolve_identity_token(state, identity_ref)
    if len(targets) != 1:
        raise ValueError(f"图上没有唯一身份 {identity_ref}")
    bind = targets[0]
    node = state.dependency_graph.nodes.get(bind)
    if node is None:
        raise ValueError(f"图上没有 {bind}")
    if node.locked:
        raise ValueError(f"dependency node is locked: {bind}")
    changed: list[str] = []
    for raw in shot_refs:
        ref = raw.strip()
        if not ref.startswith("shot:"):
            raise ValueError(f"shot.bind 只能点 shot:，收到 {ref}")
        shot = _shot_description(state, ref)
        if shot is None:
            raise ValueError(f"production 里没有 {ref}")
        graph_node = state.dependency_graph.nodes.get(ref)
        if graph_node is not None and graph_node.locked:
            raise ValueError(f"dependency node is locked: {ref}")
        if bind not in shot.spec.binds:
            shot.spec.binds = [*shot.spec.binds, bind]
            shot.media = None
        if ref not in changed:
            changed.append(ref)
    _commit_refs(state, changed, task_id="shot_bind")
    return changed


def prepare_shot_create(
    state: ProjectState,
    *,
    anchor_ref: str,
    binds: list[str],
    after_ref: str | None,
    instruction: str,
) -> TaskEnvelope:
    spec = plan_shot_create(
        state,
        anchor_ref=anchor_ref,
        binds=binds,
        after_ref=after_ref,
    )
    snapshots = DependencyGraph(state.dependency_graph).snapshot(spec.binds + [spec.anchor_ref])
    return TaskEnvelope(
        task_id=f"shot_create_{spec.shot_id}",
        agent="narrative_agent",
        goal="按 instruction 写一镜新 shot，锁死的拓扑不要改",
        scope=TaskScope(
            project_id=state.context.project_id,
            target_refs=[f"shot:{spec.shot_id}"],
        ),
        based_on_state_version=state.context.state_version,
        format_id=spec.shot_kind,
        allowed_tools=[REPAIR_SUBMIT_TOOL],
        required_outputs=["patches"],
        forbidden_actions=[
            "modify_outside_repair_scope",
            "rewrite_identity_names",
            "modify_readonly_neighbors",
            "change_locked_topology",
        ],
        acceptance_criteria=[
            "只交这一镜新 shot",
            "shot_id / order / shot_kind / source_refs / binds 与任务锁定一致",
        ],
        budget=TaskBudget(max_steps=6, max_retries=1, fallback_policy="fail"),
        input_hash=repair_input_hash(snapshots, [f"shot:{spec.shot_id}"]),
        dependency_snapshot=snapshots,
        create_shot=spec,
    )


def plan_shot_create(
    state: ProjectState,
    *,
    anchor_ref: str,
    binds: list[str],
    after_ref: str | None,
) -> ShotCreateSpec:
    kind = narrative_format(state)
    expected = _ANCHOR_FOR_KIND[kind]
    anchor = anchor_ref.strip()
    if not anchor.startswith(expected):
        raise ValueError(f"{kind} 的 anchor_ref 必须是 {expected}，收到 {anchor}")
    if anchor not in state.dependency_graph.nodes:
        raise ValueError(f"图上没有 {anchor}")
    resolved_binds: list[str] = []
    for token in binds:
        found = resolve_identity_token(state, token)
        if not found:
            raise ValueError(f"图上没有身份 {token}")
        for ref in found:
            if ref not in resolved_binds:
                resolved_binds.append(ref)
    after = (after_ref or "").strip() or None
    if after is not None:
        if _shot_description(state, after) is None:
            raise ValueError(f"production 里没有 {after}")
    order = _next_shot_order(state, after)
    action_id = None
    source_refs = [anchor]
    if kind in {"drama", "tutorial"}:
        action_id = _next_action_id(state)
        source_refs.append(f"action:{action_id}")
    if kind == "explainer":
        utterances = _utterances_for_beat(state, anchor)
        if not utterances:
            raise ValueError(f"{anchor} 上还没有 utterance，无法新开讲解镜头")
        source_refs.extend(utterances)
    elif kind == "tutorial":
        source_refs.extend(_utterances_for_anchor(state, anchor))
    return ShotCreateSpec(
        shot_id=_next_shot_id(state, action_id),
        order=order,
        shot_kind=kind,  # type: ignore[arg-type]
        source_refs=source_refs,
        binds=resolved_binds,
        anchor_ref=anchor,
        after_ref=after,
        action_id=action_id,
    )


def build_create_pack(
    state: ProjectState,
    spec: ShotCreateSpec,
    instruction: str,
) -> dict[str, Any]:
    after = spec.after_ref
    neighbor_refs: list[str] = []
    if after:
        neighbor_refs.append(after)
        following = _shot_after(state, after)
        if following is not None:
            neighbor_refs.append(following)
    else:
        last = _last_shot_ref(state)
        if last is not None:
            neighbor_refs.append(last)
    selected = set(neighbor_refs) | set(spec.binds) | {spec.anchor_ref}
    nodes: list[dict[str, Any]] = []
    for ref in spec.binds:
        nodes.append(_pack_identity(state, ref, selected))
    nodes.append(
        {
            "ref": f"shot:{spec.shot_id}",
            "kind": "shot",
            "editable": True,
            "create": True,
            "value": {
                "shot_id": spec.shot_id,
                "order": spec.order,
                "shot_kind": spec.shot_kind,
                "source_refs": spec.source_refs,
                "binds": spec.binds,
            },
        }
    )
    for ref in neighbor_refs:
        nodes.append(_pack_shot(state, ref, editable=False, selected=selected))
    return {"instruction": instruction, "create": True, "nodes": nodes}


def apply_created_shot(
    state: ProjectState,
    spec: ShotCreateSpec,
    incoming: ProductionShot,
    *,
    task_id: str,
) -> None:
    if incoming.shot_id != spec.shot_id:
        raise ValueError(f"shot:{spec.shot_id} 不能改 shot_id")
    if incoming.order != spec.order:
        raise ValueError(f"shot:{spec.shot_id} 不能改 order")
    if incoming.shot_kind != spec.shot_kind:
        raise ValueError(f"shot:{spec.shot_id} 不能改 shot_kind")
    if incoming.source_refs != spec.source_refs:
        raise ValueError(f"shot:{spec.shot_id} 不能改 source_refs")
    if incoming.binds != spec.binds:
        raise ValueError(f"shot:{spec.shot_id} 不能改 binds")
    if any(shot.spec.shot_id == spec.shot_id for shot in state.production.shots):
        raise ValueError(f"production 里已有 shot:{spec.shot_id}")
    _shift_shot_orders(state, spec.order, delta=1)
    _insert_companion_action(state, spec, incoming)
    state.production.shots.append(
        ShotDescription(spec=incoming, continuity=ShotContinuity())
    )
    state.production.shots.sort(key=lambda item: item.spec.order)
    ref = f"shot:{spec.shot_id}"
    _commit_refs(state, [ref], task_id=task_id)
    state.evaluation.elements.setdefault(ref, ElementStatus(status="pending", version=1))
    _mark_downstream_pending(state)


def remove_graph_node(state: ProjectState, ref: str) -> str:
    resolved = ref.strip()
    if resolved not in state.dependency_graph.nodes:
        matches = resolve_identity_token(state, resolved)
        if len(matches) == 1:
            resolved = matches[0]
        else:
            raise ValueError(f"图上没有 {ref}")
    node = state.dependency_graph.nodes[resolved]
    if node.locked:
        raise ValueError(f"dependency node is locked: {resolved}")
    if resolved.startswith(_IDENTITY_PREFIXES):
        leftover = list(node.dependents)
        if leftover:
            raise ValueError(f"{resolved} 仍被 {leftover} 使用")
        _drop_world_item(state, resolved)
        DependencyGraph(state.dependency_graph).remove_nodes(
            [resolved],
            task_id="graph_remove",
        )
        state.evaluation.elements.pop(resolved, None)
        state.context.state_version += 1
        return resolved
    if not resolved.startswith("shot:"):
        raise ValueError("graph.remove 只能删 cast:/prop:/place: 或 shot:")
    leftover = list(node.dependents)
    if leftover:
        raise ValueError(f"{resolved} 仍被 {leftover} 使用")
    shot = _shot_description(state, resolved)
    if shot is None:
        raise ValueError(f"production 里没有 {resolved}")
    removed_order = shot.spec.order
    action_id = _action_id_from_source(shot.spec.source_refs)
    state.production.shots = [
        item for item in state.production.shots if item.spec.shot_id != shot.spec.shot_id
    ]
    _drop_companion_action(state, action_id)
    _shift_shot_orders(state, removed_order + 1, delta=-1)
    DependencyGraph(state.dependency_graph).remove_nodes(
        [resolved],
        task_id="graph_remove",
    )
    shifted = [
        f"shot:{item.spec.shot_id}"
        for item in state.production.shots
        if item.spec.order >= removed_order
    ]
    if shifted:
        _commit_refs(state, shifted, task_id="graph_remove_order")
    else:
        state.context.state_version += 1
    state.evaluation.elements.pop(resolved, None)
    _mark_downstream_pending(state)
    return resolved


def _commit_refs(state: ProjectState, refs: list[str], *, task_id: str) -> None:
    document = _document_from_state(state)
    allowed = set(refs)
    commits = [item for item in content_commits(document) if item.ref in allowed]
    missing = allowed - {item.ref for item in commits}
    if missing:
        raise ValueError(f"无法提交这些节点：{sorted(missing)}")
    DependencyGraph(state.dependency_graph).commit_batch(
        task_id=task_id,
        produced_by="narrative_agent",
        commits=commits,
    )
    state.context.state_version += 1


def _identity_ids(state: ProjectState, kind: str) -> set[str]:
    world = state.production.world
    if kind == "cast":
        return {item.character_id for item in world.cast}
    if kind == "prop":
        return {item.prop_id for item in world.props}
    return {item.place_id for item in world.places}


def _append_tutorial_material(state: ProjectState, ident: str, name: str) -> None:
    structure = state.production.structure
    if not isinstance(structure, TutorialStructure):
        return
    if any(item.material_id == ident for item in structure.materials):
        return
    structure.materials.append(TutorialMaterial(material_id=ident, name=name))


def _drop_world_item(state: ProjectState, ref: str) -> None:
    kind, _, ident = ref.partition(":")
    world = state.production.world
    if kind == "cast":
        world.cast = [item for item in world.cast if item.character_id != ident]
    elif kind == "prop":
        world.props = [item for item in world.props if item.prop_id != ident]
        structure = state.production.structure
        if isinstance(structure, TutorialStructure):
            structure.materials = [
                item for item in structure.materials if item.material_id != ident
            ]
    elif kind == "place":
        world.places = [item for item in world.places if item.place_id != ident]


def _next_shot_order(state: ProjectState, after_ref: str | None) -> int:
    if after_ref is None:
        if not state.production.shots:
            return 1
        return max(item.spec.order for item in state.production.shots) + 1
    current = _shot_description(state, after_ref)
    if current is None:
        raise ValueError(f"production 里没有 {after_ref}")
    return current.spec.order + 1


def _next_shot_id(state: ProjectState, action_id: str | None) -> str:
    existing = {item.spec.shot_id for item in state.production.shots}
    if action_id is not None:
        candidate = f"shot_{action_id}"
        if candidate not in existing:
            return candidate
    index = 1
    while f"shot_add_{index}" in existing:
        index += 1
    return f"shot_add_{index}"


def _next_action_id(state: ProjectState) -> str:
    structure = state.production.structure
    existing: set[str] = set()
    if isinstance(structure, (DramaStructure, TutorialStructure)):
        existing = {item.action_id for item in structure.actions}
    index = 1
    while f"act_{index}" in existing:
        index += 1
    return f"act_{index}"


def _utterances_for_beat(state: ProjectState, beat_ref: str) -> list[str]:
    refs: list[str] = []
    for ref, node in state.dependency_graph.nodes.items():
        if ref.startswith("utterance:") and beat_ref in node.depends_on:
            refs.append(ref)
    return refs


def _utterances_for_anchor(state: ProjectState, anchor: str) -> list[str]:
    refs: list[str] = []
    for shot in state.production.shots:
        if anchor in shot.spec.source_refs:
            for ref in shot.spec.source_refs:
                if ref.startswith("utterance:") and ref not in refs:
                    refs.append(ref)
    return refs


def _last_shot_ref(state: ProjectState) -> str | None:
    if not state.production.shots:
        return None
    last = max(state.production.shots, key=lambda item: item.spec.order)
    return f"shot:{last.spec.shot_id}"


def _shot_after(state: ProjectState, after_ref: str) -> str | None:
    current = _shot_description(state, after_ref)
    if current is None:
        return None
    following = [
        item
        for item in state.production.shots
        if item.spec.order > current.spec.order
    ]
    if not following:
        return None
    nxt = min(following, key=lambda item: item.spec.order)
    return f"shot:{nxt.spec.shot_id}"


def _shift_shot_orders(state: ProjectState, start_order: int, *, delta: int) -> None:
    for shot in state.production.shots:
        if shot.spec.order >= start_order:
            shot.spec = shot.spec.model_copy(update={"order": shot.spec.order + delta})


def _insert_companion_action(
    state: ProjectState,
    spec: ShotCreateSpec,
    incoming: ProductionShot,
) -> None:
    if spec.action_id is None:
        return
    structure = state.production.structure
    if isinstance(structure, DramaStructure):
        scene_id = spec.anchor_ref.partition(":")[2]
        structure.actions.append(
            DramaAction(
                action_id=spec.action_id,
                order=spec.order,
                scene_id=scene_id,
                description=incoming.purpose,
                character_ids=[
                    ref.partition(":")[2]
                    for ref in spec.binds
                    if ref.startswith("cast:")
                ],
            )
        )
        for scene in structure.scenes:
            if scene.scene_id != scene_id:
                continue
            extra = [
                ref.partition(":")[2]
                for ref in spec.binds
                if ref.startswith("cast:") and ref.partition(":")[2] not in scene.character_ids
            ]
            if extra:
                scene.character_ids = [*scene.character_ids, *extra]
    elif isinstance(structure, TutorialStructure):
        step_id = spec.anchor_ref.partition(":")[2]
        subject = next(
            (ref for ref in spec.binds if ref.startswith("prop:")),
            None,
        )
        structure.actions.append(
            TutorialAction(
                action_id=spec.action_id,
                step_id=step_id,
                description=incoming.purpose,
                subject_ref=subject,
            )
        )


def _action_id_from_source(source_refs: list[str]) -> str | None:
    for ref in source_refs:
        if ref.startswith("action:"):
            return ref.partition(":")[2]
    return None


def _drop_companion_action(state: ProjectState, action_id: str | None) -> None:
    if not action_id:
        return
    still_used = any(
        f"action:{action_id}" in shot.spec.source_refs
        for shot in state.production.shots
    )
    if still_used:
        return
    structure = state.production.structure
    if isinstance(structure, (DramaStructure, TutorialStructure)):
        structure.actions = [
            item for item in structure.actions if item.action_id != action_id
        ]


def _mark_downstream_pending(state: ProjectState) -> None:
    kind = narrative_format(state)
    if kind not in {"drama", "tutorial"}:
        return
    from .world_identities import missing_identity_images

    if missing_identity_images(state):
        state.evaluation.stages.references = "ready"
        state.evaluation.stages.asset = "pending"
    else:
        state.evaluation.stages.asset = "ready"
    state.evaluation.stages.timeline = "pending"
    state.evaluation.stages.render = "pending"


def _shot_description(state: ProjectState, ref: str):
    shot_id = ref.partition(":")[2]
    for shot in state.production.shots:
        if shot.spec.shot_id == shot_id:
            return shot
    return None


def _document_from_state(state: ProjectState) -> VideoDescription:
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


def _pack_identity(state: ProjectState, ref: str, selected: set[str]) -> dict[str, Any]:
    from .shot_repair import _pack_identity as pack

    return pack(state, ref, selected)


def _pack_shot(
    state: ProjectState,
    ref: str,
    *,
    editable: bool,
    selected: set[str],
) -> dict[str, Any]:
    from .shot_repair import _pack_shot as pack

    return pack(state, ref, editable=editable, selected=selected)
