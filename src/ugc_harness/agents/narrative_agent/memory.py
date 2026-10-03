"""Projected memory: context, latest narrative layers, and ProductionShots."""

from __future__ import annotations

from typing import Any

from ...content import ProductionShot, TutorialMaterial, WorldClaim, WorldEntity
from ...knowledge.models import PriorKnowledge
from ...harness.models import ProjectState, TaskEnvelope
from .assemble import resolve_video_profile
from .models import CreativeBrief, DramaCharacter, NarrativeIdentitiesArtifact, ShotPlanArtifact


def narrative_turn(
    task: TaskEnvelope,
    instruction: str | None,
) -> dict[str, Any]:
    note = (instruction or "").strip()
    refs = list(task.scope.target_refs)
    if not note:
        note = (
            "按 instruction 只改圈中的镜头，其它镜保持原样。"
            if refs
            else "首跑：按层写完中间产物，最后写 ProductionShot，然后交卷。"
        )
    return {"instruction": note, "target_refs": refs}


def project_narrative_context(
    brief: CreativeBrief,
    prior: PriorKnowledge | None,
    task: TaskEnvelope,
    instruction: str | None,
) -> dict[str, Any]:
    payload = {
        "creative_brief": brief.model_dump(mode="json"),
        **narrative_turn(task, instruction),
    }
    if prior is not None and prior.snippets:
        payload["prior_knowledge"] = prior.model_dump(mode="json")
    return payload


def project_narrative_artifacts(
    brief: CreativeBrief,
    state: ProjectState | None,
    task: TaskEnvelope,
) -> dict[str, Any]:
    identities, shots = _from_state(brief, state)
    structure = None
    if state is not None and state.production.structure is not None:
        structure = state.production.structure.model_dump(mode="json")
    script = None
    if state is not None and state.production.voice is not None:
        script = state.production.voice.model_dump(mode="json")
    return {
        "identities": identities,
        "structure": structure,
        "script": script,
        "shots": _shot_details(shots, task),
    }


def project_narrative_memory(
    brief: CreativeBrief,
    prior: PriorKnowledge | None,
    state: ProjectState | None,
    task: TaskEnvelope,
) -> dict[str, Any]:
    return {
        "context": project_narrative_context(brief, prior, task, None),
        "artifacts": project_narrative_artifacts(brief, state, task),
    }


def narrative_opening_context(
    task: TaskEnvelope,
    *,
    brief: CreativeBrief,
    prior: PriorKnowledge | None,
    state: ProjectState | None,
    instruction: str | None,
) -> dict[str, Any]:
    return {
        "memory": {
            "context": project_narrative_context(brief, prior, task, instruction),
            "artifacts": project_narrative_artifacts(brief, state, task),
        },
        "turn": narrative_turn(task, instruction),
    }


def seed_session_from_state(
    state: ProjectState,
    brief: CreativeBrief,
) -> dict[str, Any]:
    identities, shots = _from_state(brief, state)
    if not identities and not shots:
        return {}
    payload: dict[str, Any] = {}
    book = identities_artifact_from_state(state, brief)
    if book is not None:
        payload["seed_identities"] = book.model_dump(mode="json")
    if shots:
        payload["seed_shots"] = [item.model_dump(mode="json") for item in shots]
    structure = state.production.structure
    if structure is not None:
        payload["seed_scaffold"] = structure.model_dump(mode="json")
    return payload


def identities_artifact_from_state(
    state: ProjectState,
    brief: CreativeBrief,
) -> NarrativeIdentitiesArtifact | None:
    world = state.production.world
    intent = state.creative.intent
    if not world.cast and not world.props and not world.places and not world.claims:
        if not state.production.shots:
            return None
    entities: list[WorldEntity] = []
    characters = []
    for member in world.cast:
        entities.append(
            WorldEntity(
                entity_id=member.character_id,
                name=member.name,
                kind="person",
                narrative_role=member.role,
                description=member.description,
            )
        )
        characters.append(
            DramaCharacter(
                character_id=member.character_id,
                name=member.name,
                description=member.description,
                dramatic_objective=member.dramatic_objective,
                appearance_constraints=list(member.appearance_constraints),
                voice_constraints=list(member.voice_constraints),
            )
        )
    materials = []
    for prop in world.props:
        entities.append(
            WorldEntity(
                entity_id=prop.prop_id,
                name=prop.name,
                kind="product",
                narrative_role="prop",
                description=prop.description,
            )
        )
        materials.append(
            TutorialMaterial(
                material_id=prop.prop_id,
                name=prop.name,
            )
        )
    for place in world.places:
        entities.append(
            WorldEntity(
                entity_id=place.place_id,
                name=place.name,
                kind="place",
                narrative_role="setting",
                description=place.description,
            )
        )
    claims = list(world.claims) or [
        WorldClaim(
            claim_id="wc_brief",
            statement=brief.communication.goal,
            epistemic_status="given_by_brief",
            evidence_required=False,
        )
    ]
    if not entities:
        entities.append(
            WorldEntity(
                entity_id="entity_topic",
                name=brief.topic,
                kind="concept",
                narrative_role="topic",
                description=brief.topic,
            )
        )
    from ...content import VideoWorldState

    world_state = VideoWorldState(
        topic_frame=intent.topic if intent is not None else brief.topic,
        entities=entities,
        claims=claims,
    )
    format_id = (
        intent.format_id
        if intent is not None
        else brief.production_mode
    )
    thesis = (
        intent.one_sentence_thesis
        if intent is not None
        else brief.communication.goal
    )
    profile = (
        intent.presentation
        if intent is not None
        else resolve_video_profile(brief, format_id, world_state, None)
    )
    objective = None
    if intent is not None and format_id == "tutorial":
        objective = intent.promise
    return NarrativeIdentitiesArtifact(
        one_sentence_thesis=thesis,
        world_state=world_state,
        video_profile=profile,
        characters=characters,
        objective=objective,
        materials=materials,
    )


def _from_state(
    brief: CreativeBrief,
    state: ProjectState | None,
) -> tuple[list[dict[str, Any]], list[ProductionShot]]:
    if state is None:
        return [], []
    identities: list[dict[str, Any]] = []
    world = state.production.world
    for member in world.cast:
        identities.append(
            {
                "ref": f"cast:{member.character_id}",
                "name": member.name,
                "description": member.description,
            }
        )
    for prop in world.props:
        identities.append(
            {
                "ref": f"prop:{prop.prop_id}",
                "name": prop.name,
                "description": prop.description,
            }
        )
    for place in world.places:
        identities.append(
            {
                "ref": f"place:{place.place_id}",
                "name": place.name,
                "description": place.description,
            }
        )
    shots = [item.spec for item in state.production.shots]
    return identities, shots


def _shot_details(
    shots: list[ProductionShot],
    task: TaskEnvelope,
) -> list[dict[str, Any]]:
    if not shots:
        return []
    wanted = {ref.partition(":")[2] for ref in task.scope.target_refs if ref.startswith("shot:")}
    if not wanted:
        return [item.model_dump(mode="json") for item in shots]
    selected: set[str] = set()
    by_id = {item.shot_id: item for item in shots}
    ordered = sorted(shots, key=lambda item: item.order)
    for index, item in enumerate(ordered):
        if item.shot_id not in wanted:
            continue
        selected.add(item.shot_id)
        if index > 0:
            selected.add(ordered[index - 1].shot_id)
        if index + 1 < len(ordered):
            selected.add(ordered[index + 1].shot_id)
    return [
        by_id[shot_id].model_dump(mode="json")
        for shot_id in selected
        if shot_id in by_id
    ]


def summarize_identities(book: NarrativeIdentitiesArtifact) -> dict[str, Any]:
    refs: list[dict[str, str]] = []
    for item in book.world_state.entities:
        kind = {
            "person": "cast",
            "place": "place",
            "product": "prop",
        }.get(item.kind)
        if kind is None:
            continue
        refs.append({"ref": f"{kind}:{item.entity_id}", "name": item.name})
    return {"ok": True, "identities": refs, "count": len(refs)}


def summarize_shots(shots: ShotPlanArtifact) -> dict[str, Any]:
    return {
        "ok": True,
        "spine": [
            {
                "shot_id": item.shot_id,
                "order": item.order,
                "purpose": item.purpose,
                "binds": list(item.binds),
            }
            for item in shots.shots
        ],
        "count": len(shots.shots),
    }
