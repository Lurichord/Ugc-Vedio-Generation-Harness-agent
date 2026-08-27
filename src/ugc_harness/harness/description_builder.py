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
from ..content import ProductionShot, TutorialVisualSpec
from .description import (
    CastMember,
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
    element_refs,
)
from .models import ElementStatus, ExecutionState


def build_video_description(artifact: NarrativeArtifact) -> VideoDescription:
    return VideoDescription(
        intent=_intent(artifact),
        world=_world(artifact),
        structure=_structure(artifact),
        voice=_voice(artifact),
        shots=_shots(artifact),
        deliverable=_deliverable(artifact),
    )


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
    cast: list[CastMember] = []
    aroll = planning.world_state.aroll_character
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
        entity_names = {
            entity.entity_id: entity.name
            for entity in planning.world_state.entities
        }
        for character in planning.characters:
            cast.append(
                CastMember(
                    character_id=character.character_id,
                    name=character.name or entity_names.get(
                        character.character_id, character.character_id
                    ),
                    role="performer",
                    description=character.description,
                    dramatic_objective=character.dramatic_objective,
                    appearance_constraints=list(character.appearance_constraints),
                    voice_constraints=list(character.voice_constraints),
                )
            )
    return VideoWorld(state=planning.world_state, cast=cast)


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


def _voice(artifact: NarrativeArtifact) -> VoiceDesign | None:
    script = artifact.script
    if isinstance(script, ScriptArtifact):
        return VoiceDesign(
            utterances=[
                Utterance(
                    utterance_id=segment.script_segment_id,
                    anchor_ref=f"beat:{segment.planned_beat_id}",
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
    """Tail-frame continuation is a generation constraint, not an edit.

    Continue only when the shots stay inside one dramatic scene or one
    tutorial step with an unchanged camera angle; a location or camera jump
    must start fresh or the previous tail frame locks the model into the
    wrong framing.
    """

    if previous is None or current.shot_kind == "explainer":
        return ShotContinuity()
    if current.shot_kind == "drama":
        if (
            previous.payload.payload_type == "drama"
            and current.payload.payload_type == "drama"
            and previous.payload.scene_id == current.payload.scene_id
        ):
            return ShotContinuity(
                join="continue_from_previous",
                previous_shot_id=previous.shot_id,
            )
        return ShotContinuity()
    if (
        previous.payload.payload_type == "tutorial"
        and current.payload.payload_type == "tutorial"
        and previous.payload.step_id == current.payload.step_id
        and isinstance(previous.visual, TutorialVisualSpec)
        and isinstance(current.visual, TutorialVisualSpec)
        and previous.visual.camera_angle == current.visual.camera_angle
    ):
        return ShotContinuity(
            join="continue_from_previous",
            previous_shot_id=previous.shot_id,
        )
    return ShotContinuity()


def _deliverable(artifact: NarrativeArtifact) -> Deliverable | None:
    script = artifact.script
    if isinstance(script, ScriptArtifact) and script.title_options:
        return Deliverable(
            publish=PublishMetadata(title_options=list(script.title_options))
        )
    return None
