"""Local shot repair: pack circled shots for the generic agent and apply patches."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..agents.narrative_agent.models import NarrativeArtifact
from ..content import ProductionShot
from .dependencies import DependencyGraph, NodeCommit
from .dependency_builders import content_commits
from .description import VideoDescription
from .models import (
    ArtifactRef,
    ProjectState,
    ShotDescription,
    StatePatch,
    TaskEnvelope,
)

REPAIR_SUBMIT_TOOL = "repair.submit_candidate"

_STRUCTURE_PREFIXES = ("beat:", "planned_beat:", "scene:", "step:")
_IDENTITY_PREFIXES = ("cast:", "prop:", "place:")
_UTTERANCE_PREFIXES = ("utterance:", "script_segment:")
_LOCKED_SHOT_FIELDS = ("shot_id", "order", "shot_kind", "source_refs", "binds")


class ShotPatch(BaseModel):
    ref: str
    value: dict[str, Any]


class RepairCandidate(BaseModel):
    patches: list[ShotPatch] = Field(min_length=1)


def resolve_shot_refs(state: ProjectState, target_refs: list[str]) -> list[str]:
    """Map intake targets to shot refs via production and graph edges."""

    found: list[str] = []
    for ref in target_refs:
        resolved = _resolve_ref(state, ref) or ref
        if resolved.startswith("shot:") and resolved not in found:
            found.append(resolved)
            continue
        for shot_ref in _shots_linked_to(state, resolved):
            if shot_ref not in found:
                found.append(shot_ref)
    return found


def build_repair_pack(
    state: ProjectState,
    shot_refs: list[str],
    instruction: str,
) -> dict[str, Any]:
    editable = list(shot_refs)
    missing = [ref for ref in editable if _shot_description(state, ref) is None]
    if missing:
        raise ValueError(f"production 里没有这些 shot：{missing}")
    locked = [
        ref
        for ref in editable
        if (node := state.dependency_graph.nodes.get(ref)) is not None and node.locked
    ]
    if locked:
        raise ValueError(f"dependency node is locked: {locked[0]}")

    neighbor_refs = _neighbor_shot_refs(state, editable)
    identity_refs = _identity_refs_for(state, editable)
    selected = set(editable) | set(neighbor_refs) | set(identity_refs)
    nodes: list[dict[str, Any]] = []
    for ref in identity_refs:
        nodes.append(_pack_identity(state, ref, selected))
    for ref in editable:
        nodes.append(_pack_shot(state, ref, editable=True, selected=selected))
    for ref in neighbor_refs:
        nodes.append(_pack_shot(state, ref, editable=False, selected=selected))
    return {"instruction": instruction, "nodes": nodes}


def apply_shot_patches(
    state: ProjectState,
    candidate: RepairCandidate,
    shot_refs: list[str],
) -> list[NodeCommit]:
    allowed = set(shot_refs)
    submitted = [item.ref for item in candidate.patches]
    extra = [ref for ref in submitted if ref not in allowed]
    if extra:
        raise ValueError(f"repair output is outside editable shots: {sorted(extra)}")
    missing = allowed - set(submitted)
    if missing:
        raise ValueError(f"repair output is missing target refs: {sorted(missing)}")

    by_id = {shot.spec.shot_id: shot for shot in state.production.shots}
    for item in candidate.patches:
        shot_id = item.ref.partition(":")[2]
        current = by_id.get(shot_id)
        if current is None:
            raise ValueError(f"production 里没有 {item.ref}")
        incoming = ProductionShot.model_validate(item.value)
        previous = current.spec
        for field in _LOCKED_SHOT_FIELDS:
            if getattr(incoming, field) != getattr(previous, field):
                raise ValueError(f"{item.ref} 不能改 {field}")
        unknown = [
            bind
            for bind in incoming.binds
            if bind not in state.dependency_graph.nodes
            and _world_identity(state, bind) is None
        ]
        if unknown:
            raise ValueError(f"{item.ref} binds 指向不存在的身份：{unknown}")
        current.spec = incoming
        current.media = None

    document = _document_from_state(state)
    commits = [item for item in content_commits(document) if item.ref in allowed]
    return commits


def run_shot_repair(
    project_dir: Path,
    task: TaskEnvelope,
    instruction: str,
    *,
    tool_model: object | None = None,
) -> None:
    from ..agents.generic import (
        CompletionSpec,
        EnvironmentToolModel,
        GenericAgent,
        RegistryTool,
        RegistryToolTransport,
    )
    from .repair import (
        load_project_state,
        select_repair_commits,
        write_project_state,
    )

    directory = Path(project_dir)
    state = load_project_state(directory)
    shot_refs = [ref for ref in task.scope.target_refs if ref.startswith("shot:")]
    if not shot_refs:
        shot_refs = resolve_shot_refs(state, task.scope.target_refs)
    if not shot_refs:
        raise ValueError("shot repair 需要 shot target_refs")

    pack = build_repair_pack(state, shot_refs, instruction or "")
    envelope = task
    if set(envelope.scope.target_refs) != set(shot_refs):
        envelope = task.model_copy(
            update={
                "scope": task.scope.model_copy(update={"target_refs": shot_refs})
            }
        )

    def submit(patches: list[dict[str, Any]]) -> RepairCandidate:
        return RepairCandidate.model_validate({"patches": patches})

    agent = GenericAgent(
        tool_model=tool_model or EnvironmentToolModel(),
        transport_factory=lambda _task, _kwargs: RegistryToolTransport(
            [
                RegistryTool(
                    name=REPAIR_SUBMIT_TOOL,
                    description="提交圈中 shot 的补丁。只交 editable 的 shot。",
                    handler=lambda patches: submit(patches),
                    parameters={
                        "type": "object",
                        "properties": {
                            "patches": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "ref": {"type": "string"},
                                        "value": {"type": "object"},
                                    },
                                    "required": ["ref", "value"],
                                    "additionalProperties": False,
                                },
                            }
                        },
                        "required": ["patches"],
                        "additionalProperties": False,
                    },
                )
            ]
        ),
        candidate_type=RepairCandidate,
        completion_builder=lambda _task, _kwargs: CompletionSpec(
            artifact_refs=[
                ArtifactRef(kind="shot", id=ref.partition(":")[2])
                for ref in shot_refs
            ],
            state_patch=StatePatch(),
            evaluation_target=shot_refs[0],
        ),
        context_builder=lambda _task, _kwargs: {"repair_pack": pack},
    )
    execution = agent.run(envelope)
    candidate = execution.candidate
    if execution.result.status != "completed" or candidate is None:
        raise RuntimeError(execution.result.error or "shot repair 没有提交补丁")
    if not isinstance(candidate, RepairCandidate):
        raise RuntimeError("shot repair 返回了错误的候选类型")

    commits = apply_shot_patches(state, candidate, shot_refs)
    graph = DependencyGraph(state.dependency_graph)
    commits = select_repair_commits(graph, commits, envelope)
    graph.commit_batch(
        task_id=envelope.task_id,
        produced_by="narrative_agent",
        commits=commits,
    )
    state.context.state_version += 1
    format_id = (
        state.context.constraints.get("narrative_format")
        or state.context.constraints.get("production_mode")
    )
    if format_id in {"drama", "tutorial"}:
        state.evaluation.stages.asset = "ready"
        state.evaluation.stages.timeline = "pending"
        state.evaluation.stages.render = "pending"
    write_project_state(directory, state)
    _sync_narrative_artifact_shots(directory, state)


def _shots_linked_to(state: ProjectState, ref: str) -> list[str]:
    linked: list[str] = []
    for shot in state.production.shots:
        spec = shot.spec
        shot_ref = f"shot:{spec.shot_id}"
        if ref.startswith(_STRUCTURE_PREFIXES) and ref in spec.source_refs:
            linked.append(shot_ref)
        elif ref.startswith(_UTTERANCE_PREFIXES) and ref in spec.source_refs:
            linked.append(shot_ref)
        elif ref.startswith(_IDENTITY_PREFIXES) and ref in spec.binds:
            linked.append(shot_ref)
    node = state.dependency_graph.nodes.get(ref)
    if node is not None:
        for dep in node.dependents:
            if dep.startswith("shot:") and dep not in linked:
                linked.append(dep)
    for other in state.dependency_graph.nodes.values():
        if other.ref.startswith("shot:") and ref in other.depends_on:
            if other.ref not in linked:
                linked.append(other.ref)
    return linked


def _shot_description(state: ProjectState, ref: str) -> ShotDescription | None:
    shot_id = ref.partition(":")[2]
    for shot in state.production.shots:
        if shot.spec.shot_id == shot_id:
            return shot
    return None


def _neighbor_shot_refs(state: ProjectState, editable: list[str]) -> list[str]:
    ordered = sorted(state.production.shots, key=lambda item: item.spec.order)
    index_by_ref = {
        f"shot:{shot.spec.shot_id}": index for index, shot in enumerate(ordered)
    }
    neighbors: list[str] = []
    editable_set = set(editable)
    for ref in editable:
        index = index_by_ref.get(ref)
        if index is None:
            continue
        for candidate in (index - 1, index + 1):
            if candidate < 0 or candidate >= len(ordered):
                continue
            neighbor = f"shot:{ordered[candidate].spec.shot_id}"
            if neighbor not in editable_set and neighbor not in neighbors:
                neighbors.append(neighbor)
    return neighbors


def _identity_refs_for(state: ProjectState, shot_refs: list[str]) -> list[str]:
    refs: list[str] = []
    for shot_ref in shot_refs:
        shot = _shot_description(state, shot_ref)
        if shot is None:
            continue
        for bind in shot.spec.binds:
            if bind not in refs:
                refs.append(bind)
    return refs


def _pack_shot(
    state: ProjectState,
    ref: str,
    *,
    editable: bool,
    selected: set[str],
) -> dict[str, Any]:
    shot = _shot_description(state, ref)
    if shot is None:
        raise ValueError(f"production 里没有 {ref}")
    node = state.dependency_graph.nodes.get(ref)
    payload: dict[str, Any] = {
        "ref": ref,
        "kind": "shot",
        "editable": editable,
        "value": shot.spec.model_dump(mode="json"),
    }
    if node is not None:
        payload["depends_on"] = [item for item in node.depends_on if item in selected]
        payload["edges"] = [
            {"to": edge.to, "family": edge.family}
            for edge in node.edges
            if edge.to in selected
        ]
    return payload


def _pack_identity(
    state: ProjectState, ref: str, selected: set[str]
) -> dict[str, Any]:
    value = _world_identity(state, ref) or {}
    node = state.dependency_graph.nodes.get(ref)
    payload: dict[str, Any] = {
        "ref": ref,
        "kind": ref.partition(":")[0],
        "editable": False,
        "value": value,
    }
    if node is not None:
        payload["dependents"] = [
            item for item in node.dependents if item in selected
        ]
    return payload


def _resolve_ref(state: ProjectState, ref: str) -> str | None:
    nodes = state.dependency_graph.nodes
    if ref in nodes:
        return ref
    kind, _, ident = ref.partition(":")
    if not ident:
        return None
    aliases = {
        "planned_beat": ("beat",),
        "beat": ("planned_beat",),
        "script_segment": ("utterance",),
        "utterance": ("script_segment",),
    }
    for prefix in aliases.get(kind, ()):
        candidate = f"{prefix}:{ident}"
        if candidate in nodes:
            return candidate
    return None


def _world_identity(state: ProjectState, ref: str) -> dict[str, Any] | None:
    kind, _, ident = ref.partition(":")
    world = state.production.world
    if kind == "cast":
        items = world.cast
        key = "character_id"
    elif kind == "prop":
        items = world.props
        key = "prop_id"
    elif kind == "place":
        items = world.places
        key = "place_id"
    else:
        return None
    for item in items:
        if getattr(item, key) == ident:
            return item.model_dump(mode="json")
    return None


def _document_from_state(state: ProjectState) -> VideoDescription:
    intent = state.creative.intent
    structure = state.production.structure
    if intent is None or structure is None:
        raise ValueError("production 不完整，无法提交 shot 补丁")
    return VideoDescription(
        intent=intent,
        world=state.production.world,
        structure=structure,
        voice=state.production.voice,
        shots=state.production.shots,
        timeline=state.production.timeline,
        deliverable=state.production.deliverable,
    )


def _sync_narrative_artifact_shots(project_dir: Path, state: ProjectState) -> None:
    path = project_dir / "narrative_artifact.json"
    if not path.is_file():
        return
    artifact = NarrativeArtifact.model_validate_json(path.read_text(encoding="utf-8"))
    if artifact.shots is None:
        return
    by_id = {shot.spec.shot_id: shot.spec for shot in state.production.shots}
    updated = []
    for shot in artifact.shots.shots:
        updated.append(by_id.get(shot.shot_id, shot))
    artifact.shots.shots = updated
    path.write_text(artifact.model_dump_json(indent=2), encoding="utf-8")
