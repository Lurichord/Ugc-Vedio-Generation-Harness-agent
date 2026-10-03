"""Build the VideoDescription document from committed narrative artifacts.

The narrative artifact remains the agent-facing contract; this builder turns
an approved artifact into the description document that ProjectState carries
as the single source of truth. Downstream stages append realization data to
the same document instead of growing parallel artifact files.
"""

from __future__ import annotations

from ..agents.narrative_agent.models import (
    DramaPlanningArtifact,
    NarrativeArtifact,
    PlanningArtifact,
    ScriptArtifact,
    TutorialPlanningArtifact,
    TutorialScriptArtifact,
)
from ..content import DramaVisualSpec, ProductionShot, TutorialVisualSpec
from .description import (
    CastMember,
    CreativeBriefRecord,
    Deliverable,
    DramaStructure,
    ExplainerStructure,
    PublishMetadata,
    ResultDefinition,
    ShotContinuity,
    ShotDescription,
    TutorialStructure,
    Utterance,
    VideoDescription,
    VideoIntent,
    VideoStructure,
    VideoWorld,
    VoiceDesign,
    WorldPlace,
    WorldProp,
    element_refs,
)
from .models import ElementStatus, ExecutionState, ProjectState


def build_video_description(artifact: NarrativeArtifact) -> VideoDescription:
    return VideoDescription(
        intent=_intent(artifact),
        world=_world(artifact),
        structure=_structure(artifact),
        voice=_voice(artifact),
        shots=_shots(artifact),
        deliverable=_deliverable(artifact),
    )


def apply_narrative_document(
    state: ProjectState, artifact: NarrativeArtifact
) -> None:
    """Write creative + production from an approved narrative artifact."""

    document = build_video_description(artifact)
    state.apply_description(document)
    state.creative.brief = CreativeBriefRecord.model_validate(
        artifact.brief.model_dump(mode="json")
    )
    state.evaluation.elements = initial_execution_state(document).elements


def refresh_shot_continuity(state: ProjectState) -> None:
    """Recompute 接/承/断 from current shot specs.

    Existing projects may still store scene-only continue joins. Asset
    listing, generation, and repair expansion should follow the live
    specs rather than that stale field.
    """

    previous = None
    for shot in sorted(state.production.shots, key=lambda item: item.spec.order):
        shot.continuity = _continuity(previous, shot.spec)
        previous = shot.spec


def initial_execution_state(description: VideoDescription) -> ExecutionState:
    """Tag every committed description element as passed at version 1."""

    return ExecutionState(
        elements={
            ref: ElementStatus(status="passed", version=1)
            for ref in element_refs(description)
        }
    )


def _intent(artifact: NarrativeArtifact) -> VideoIntent:
    planning = artifact.planning
    brief = artifact.brief
    if isinstance(planning, DramaPlanningArtifact):
        promise = planning.premise
    elif isinstance(planning, TutorialPlanningArtifact):
        promise = planning.objective
    else:
        promise = planning.one_sentence_thesis
    return VideoIntent(
        format_id=planning.planning_type,
        topic=brief.topic,
        one_sentence_thesis=planning.one_sentence_thesis,
        promise=promise,
        audience=brief.audience,
        communication=brief.communication,
        target=brief.target,
        content_policy=brief.content_policy,
        presentation=planning.video_profile,
    )


def _world(artifact: NarrativeArtifact) -> VideoWorld:
    planning = artifact.planning
    world_state = planning.world_state
    entities = {entity.entity_id: entity for entity in world_state.entities}
    cast: list[CastMember] = []
    aroll = world_state.aroll_character
    if aroll is not None:
        cast.append(
            CastMember(
                character_id=aroll.character_id,
                name=aroll.character_id,
                role="narrator",
                description=aroll.visual_description,
                voice_profile=aroll.voice_profile,
            )
        )
    if isinstance(planning, DramaPlanningArtifact):
        for character in planning.characters:
            entity = entities.get(character.character_id)
            cast.append(
                CastMember(
                    character_id=character.character_id,
                    name=character.name
                    or (entity.name if entity else character.character_id),
                    role="performer",
                    description=character.description,
                    dramatic_objective=character.dramatic_objective,
                    appearance_constraints=list(character.appearance_constraints),
                    voice_constraints=list(character.voice_constraints),
                )
            )
    places: list[WorldPlace] = []
    if isinstance(planning, DramaPlanningArtifact):
        seen: set[str] = set()
        for scene in planning.scenes:
            if scene.location_id in seen:
                continue
            seen.add(scene.location_id)
            entity = entities.get(scene.location_id)
            places.append(
                WorldPlace(
                    place_id=scene.location_id,
                    name=entity.name if entity else scene.location_id,
                    description=entity.description if entity else "",
                )
            )
    props: list[WorldProp] = []
    if isinstance(planning, TutorialPlanningArtifact):
        for material in planning.materials:
            props.append(
                WorldProp(
                    prop_id=material.material_id,
                    name=material.name,
                    description=material.quantity or material.name,
                )
            )
    seen_cast = {member.character_id for member in cast}
    seen_places = {item.place_id for item in places}
    seen_props = {item.prop_id for item in props}
    seen_prop_names = {item.name for item in props}
    for entity in world_state.entities:
        if entity.kind == "person" and entity.entity_id not in seen_cast:
            cast.append(
                CastMember(
                    character_id=entity.entity_id,
                    name=entity.name,
                    role="performer",
                    description=entity.description,
                )
            )
            seen_cast.add(entity.entity_id)
        elif entity.kind == "place" and entity.entity_id not in seen_places:
            places.append(
                WorldPlace(
                    place_id=entity.entity_id,
                    name=entity.name,
                    description=entity.description,
                )
            )
            seen_places.add(entity.entity_id)
        elif (
            entity.kind == "product"
            and entity.entity_id not in seen_props
            and entity.name not in seen_prop_names
        ):
            props.append(
                WorldProp(
                    prop_id=entity.entity_id,
                    name=entity.name,
                    description=entity.description,
                )
            )
            seen_props.add(entity.entity_id)
            seen_prop_names.add(entity.name)
    return VideoWorld(
        cast=cast,
        props=props,
        places=places,
        claims=list(world_state.claims),
    )


def _structure(artifact: NarrativeArtifact) -> VideoStructure:
    planning = artifact.planning
    if isinstance(planning, PlanningArtifact):
        return ExplainerStructure(
            narrative_pattern=planning.narrative_pattern,
            sections=list(planning.sections),
            beats=list(planning.beats),
        )
    if isinstance(planning, DramaPlanningArtifact):
        return DramaStructure(
            premise=planning.premise,
            scenes=list(planning.scenes),
            actions=list(planning.actions),
        )
    assert isinstance(planning, TutorialPlanningArtifact)
    return TutorialStructure(
        objective=planning.objective,
        result=ResultDefinition(
            description=planning.objective,
            success_criteria=list(planning.coverage_requirements),
        ),
        materials=list(planning.materials),
        tools=list(planning.tools),
        steps=list(planning.steps),
        actions=list(planning.actions),
        coverage_requirements=list(planning.coverage_requirements),
    )


def _speaker_ref(artifact: NarrativeArtifact) -> str | None:
    aroll = artifact.planning.world_state.aroll_character
    if aroll is None:
        return None
    return f"cast:{aroll.character_id}"


def _voice(artifact: NarrativeArtifact) -> VoiceDesign | None:
    script = artifact.script
    speaker_ref = _speaker_ref(artifact)
    if isinstance(script, ScriptArtifact):
        return VoiceDesign(
            utterances=[
                Utterance(
                    utterance_id=segment.script_segment_id,
                    anchor_ref=f"beat:{segment.planned_beat_id}",
                    speaker_ref=speaker_ref,
                    placement="full",
                    text=segment.text,
                    delivery=segment.delivery_hint,
                )
                for segment in script.segments
            ]
        )
    if isinstance(script, TutorialScriptArtifact):
        return VoiceDesign(
            utterances=[
                Utterance(
                    utterance_id=segment.explanation_segment_id,
                    anchor_ref=f"step:{segment.step_id}",
                    speaker_ref=speaker_ref,
                    placement=segment.placement,
                    text=segment.text,
                )
                for segment in script.segments
            ]
        )
    return None


def _shots(artifact: NarrativeArtifact) -> list[ShotDescription]:
    if artifact.shots is None:
        return []
    descriptions: list[ShotDescription] = []
    previous: ProductionShot | None = None
    for shot in artifact.shots.shots:
        descriptions.append(
            ShotDescription(
                spec=shot,
                continuity=_continuity(previous, shot),
            )
        )
        previous = shot
    return descriptions


def _continuity(
    previous: ProductionShot | None,
    current: ProductionShot,
) -> ShotContinuity:
    """Tail-frame join is a generation constraint, not an edit.

    接: same scene, place, and overlapping cast (or tutorial same camera
    with the same step or a shared workpiece). The previous last frame
    leads, and the prompt names it and locks pose and camera.
    承: not a hard continue, but the previous clip still matters because
    the place, people, or workpiece overlap. The last frame is named in
    the prompt as a look/wardrobe/set/material reference, not a pose
    lock, and must not occupy slot 1.
    断: new place and almost no shared people; generate without a tail.
    """

    if previous is None or current.shot_kind == "explainer":
        return ShotContinuity()
    if current.shot_kind == "drama":
        return _drama_continuity(previous, current)
    if current.shot_kind == "tutorial":
        return _tutorial_continuity(previous, current)
    return ShotContinuity()


def _drama_continuity(
    previous: ProductionShot,
    current: ProductionShot,
) -> ShotContinuity:
    if (
        previous.payload.payload_type != "drama"
        or current.payload.payload_type != "drama"
    ):
        return ShotContinuity()
    same_scene = previous.payload.scene_id == current.payload.scene_id
    same_place = _same_location(previous, current)
    shared_cast = bool(_drama_characters(previous) & _drama_characters(current))
    if same_scene and same_place and shared_cast:
        return ShotContinuity(
            join="continue_from_previous",
            previous_shot_id=previous.shot_id,
        )
    if same_place or shared_cast:
        return ShotContinuity(
            join="bridge_from_previous",
            previous_shot_id=previous.shot_id,
        )
    return ShotContinuity()


def _tutorial_continuity(
    previous: ProductionShot,
    current: ProductionShot,
) -> ShotContinuity:
    if (
        previous.payload.payload_type != "tutorial"
        or current.payload.payload_type != "tutorial"
    ):
        return ShotContinuity()
    same_step = previous.payload.step_id == current.payload.step_id
    same_camera = (
        isinstance(previous.visual, TutorialVisualSpec)
        and isinstance(current.visual, TutorialVisualSpec)
        and previous.visual.camera_angle == current.visual.camera_angle
    )
    overlap = same_step or _tutorial_overlap(previous, current)
    if overlap and same_camera:
        return ShotContinuity(
            join="continue_from_previous",
            previous_shot_id=previous.shot_id,
        )
    if overlap:
        return ShotContinuity(
            join="bridge_from_previous",
            previous_shot_id=previous.shot_id,
        )
    return ShotContinuity()


def _same_location(previous: ProductionShot, current: ProductionShot) -> bool:
    left = _drama_location(previous)
    right = _drama_location(current)
    return bool(left) and left == right


def _drama_location(shot: ProductionShot) -> str:
    visual = shot.visual
    if isinstance(visual, DramaVisualSpec) and visual.location_id:
        return visual.location_id
    for bind in shot.binds:
        if bind.startswith("place:"):
            return bind.partition(":")[2]
    return ""


def _drama_characters(shot: ProductionShot) -> set[str]:
    visual = shot.visual
    if isinstance(visual, DramaVisualSpec) and visual.character_ids:
        return set(visual.character_ids)
    return {
        bind.partition(":")[2]
        for bind in shot.binds
        if bind.startswith("cast:")
    }


def _tutorial_overlap(previous: ProductionShot, current: ProductionShot) -> bool:
    left = _tutorial_subject(previous)
    right = _tutorial_subject(current)
    if left and left == right:
        return True
    return bool(set(previous.binds) & set(current.binds))


def _tutorial_subject(shot: ProductionShot) -> str:
    visual = shot.visual
    if isinstance(visual, TutorialVisualSpec):
        return visual.subject_ref
    return ""


def _deliverable(artifact: NarrativeArtifact) -> Deliverable | None:
    script = artifact.script
    if isinstance(script, ScriptArtifact) and script.title_options:
        return Deliverable(
            publish=PublishMetadata(title_options=list(script.title_options))
        )
    return None
