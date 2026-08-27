from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import Field, model_validator

from ...content import (
    AudienceSpec,
    CommunicationSpec,
    ContentPolicy,
    DramaAction,
    DramaCharacter,
    DramaScene,
    PlannedBeat,
    ProductionMode,
    ProductionShot,
    ScriptSegment,
    Section,
    TargetSpec,
    TutorialAction,
    TutorialExplanationSegment,
    TutorialMaterial,
    TutorialStep,
    VideoWorldState,
)
from ...profiles.models import VideoProfileDecision, VideoProfileRequest
from ...shared.models import StrictModel


class CreativeBrief(StrictModel):
    project_id: str
    project_name: str | None = None
    topic: str = Field(min_length=2)
    target: TargetSpec
    audience: AudienceSpec
    communication: CommunicationSpec
    production_mode: ProductionMode = "auto"
    video_profile: VideoProfileRequest = "auto"
    content_policy: ContentPolicy = Field(default_factory=ContentPolicy)


def _validate_character_consistency(
    world_state: VideoWorldState,
    video_profile: VideoProfileDecision,
) -> None:
    character = world_state.aroll_character
    if video_profile.resolved in {"a_roll", "ab_roll"}:
        if character is None:
            raise ValueError("speaker-led planning requires world_state.aroll_character")
        if character.character_id != video_profile.character_id:
            raise ValueError("world-state character_id must match video_profile")
        if character.visual_description != video_profile.character_description:
            raise ValueError(
                "world-state character description must match video_profile"
            )
    elif character is not None:
        raise ValueError("b_roll planning cannot define an A-roll character")


class SectionPlanArtifact(StrictModel):
    """World state, video profile, and the three-section skeleton."""

    planning_type: Literal["explainer"] = "explainer"
    narrative_pattern: str
    one_sentence_thesis: str
    world_state: VideoWorldState
    video_profile: VideoProfileDecision
    sections: list[Section] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_consistency(self) -> "SectionPlanArtifact":
        _validate_character_consistency(self.world_state, self.video_profile)
        return self


class BeatPlanArtifact(StrictModel):
    """Planned beats expanding an approved section plan."""

    beats: list[PlannedBeat] = Field(min_length=6, max_length=24)

    @model_validator(mode="after")
    def validate_beats(self) -> "BeatPlanArtifact":
        ids = [beat.planned_beat_id for beat in self.beats]
        if len(ids) != len(set(ids)):
            raise ValueError("planned_beat_id values must be unique")
        if [beat.order for beat in self.beats] != list(range(1, len(self.beats) + 1)):
            raise ValueError("beat order must be contiguous and start at 1")
        return self


class PlanningArtifact(StrictModel):
    planning_type: Literal["explainer"] = "explainer"
    narrative_pattern: str
    one_sentence_thesis: str
    world_state: VideoWorldState
    video_profile: VideoProfileDecision
    sections: list[Section] = Field(min_length=3, max_length=3)
    beats: list[PlannedBeat] = Field(min_length=6, max_length=24)

    @classmethod
    def from_parts(
        cls,
        section_plan: SectionPlanArtifact,
        beat_plan: BeatPlanArtifact,
    ) -> "PlanningArtifact":
        return cls.model_validate(
            {
                **section_plan.model_dump(mode="json"),
                "beats": beat_plan.model_dump(mode="json")["beats"],
            }
        )

    @model_validator(mode="after")
    def validate_graph_references(self) -> "PlanningArtifact":
        section_ids = {section.section_id for section in self.sections}
        unknown = {
            beat.section_id for beat in self.beats if beat.section_id not in section_ids
        }
        if unknown:
            raise ValueError(f"beats reference unknown sections: {sorted(unknown)}")
        ids = [beat.planned_beat_id for beat in self.beats]
        if len(ids) != len(set(ids)):
            raise ValueError("planned_beat_id values must be unique")
        if [beat.order for beat in self.beats] != list(range(1, len(self.beats) + 1)):
            raise ValueError("beat order must be contiguous and start at 1")
        _validate_character_consistency(self.world_state, self.video_profile)
        return self


class DramaWorldArtifact(StrictModel):
    """World and cast established before story structure is generated."""

    one_sentence_thesis: str
    world_state: VideoWorldState
    video_profile: VideoProfileDecision
    characters: list[DramaCharacter] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_character_entities(self) -> "DramaWorldArtifact":
        character_ids = [item.character_id for item in self.characters]
        if len(character_ids) != len(set(character_ids)):
            raise ValueError("character_id values must be unique")
        entity_ids = {item.entity_id for item in self.world_state.entities}
        if unknown := set(character_ids) - entity_ids:
            raise ValueError(
                f"drama characters missing from world_state: {sorted(unknown)}"
            )
        return self


class DramaStoryArtifact(StrictModel):
    premise: str
    scenes: list[DramaScene] = Field(min_length=2, max_length=12)

    @model_validator(mode="after")
    def validate_scenes(self) -> "DramaStoryArtifact":
        ids = [item.scene_id for item in self.scenes]
        if len(ids) != len(set(ids)):
            raise ValueError("scene_id values must be unique")
        if [item.order for item in self.scenes] != list(
            range(1, len(self.scenes) + 1)
        ):
            raise ValueError("drama scene order must be contiguous and start at 1")
        return self


class DramaActionPlanArtifact(StrictModel):
    actions: list[DramaAction] = Field(min_length=4, max_length=24)

    @model_validator(mode="after")
    def validate_actions(self) -> "DramaActionPlanArtifact":
        ids = [item.action_id for item in self.actions]
        if len(ids) != len(set(ids)):
            raise ValueError("action_id values must be unique")
        if [item.order for item in self.actions] != list(
            range(1, len(self.actions) + 1)
        ):
            raise ValueError("drama action order must be contiguous and start at 1")
        return self


class DramaPlanningArtifact(StrictModel):
    """Schema boundary for the future drama pack; no tools are installed yet."""

    planning_type: Literal["drama"] = "drama"
    one_sentence_thesis: str
    world_state: VideoWorldState
    video_profile: VideoProfileDecision
    premise: str
    characters: list[DramaCharacter] = Field(min_length=1)
    scenes: list[DramaScene] = Field(min_length=2, max_length=12)
    actions: list[DramaAction] = Field(min_length=4, max_length=24)

    @classmethod
    def from_parts(
        cls,
        world: DramaWorldArtifact,
        story: DramaStoryArtifact,
        action_plan: DramaActionPlanArtifact,
    ) -> "DramaPlanningArtifact":
        return cls(
            one_sentence_thesis=world.one_sentence_thesis,
            world_state=world.world_state,
            video_profile=world.video_profile,
            premise=story.premise,
            characters=world.characters,
            scenes=story.scenes,
            actions=action_plan.actions,
        )

    @model_validator(mode="after")
    def validate_drama_references(self) -> "DramaPlanningArtifact":
        character_ids = [item.character_id for item in self.characters]
        scene_ids = [item.scene_id for item in self.scenes]
        action_ids = [item.action_id for item in self.actions]
        for label, values in (
            ("character_id", character_ids),
            ("scene_id", scene_ids),
            ("action_id", action_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} values must be unique")
        if [scene.order for scene in self.scenes] != list(
            range(1, len(self.scenes) + 1)
        ):
            raise ValueError("drama scene order must be contiguous and start at 1")
        if [action.order for action in self.actions] != list(
            range(1, len(self.actions) + 1)
        ):
            raise ValueError("drama action order must be contiguous and start at 1")
        known_characters = set(character_ids)
        known_scenes = set(scene_ids)
        world_entities = {item.entity_id for item in self.world_state.entities}
        referenced_characters = {
            character_id
            for scene in self.scenes
            for character_id in scene.character_ids
        } | {
            character_id
            for action in self.actions
            for character_id in action.character_ids
        }
        if unknown := referenced_characters - known_characters:
            raise ValueError(f"drama references unknown characters: {sorted(unknown)}")
        if unknown := {item.scene_id for item in self.actions} - known_scenes:
            raise ValueError(f"actions reference unknown scenes: {sorted(unknown)}")
        world_refs = known_characters | {item.location_id for item in self.scenes}
        if unknown := world_refs - world_entities:
            raise ValueError(
                f"drama references entities missing from world_state: {sorted(unknown)}"
            )
        return self


class TutorialDefinitionArtifact(StrictModel):
    one_sentence_thesis: str
    world_state: VideoWorldState
    video_profile: VideoProfileDecision
    objective: str
    materials: list[TutorialMaterial] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    coverage_requirements: list[str] = Field(default_factory=list)


class TutorialProcedureArtifact(StrictModel):
    steps: list[TutorialStep] = Field(min_length=1)
    actions: list[TutorialAction] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_references(self) -> "TutorialProcedureArtifact":
        step_ids = [item.step_id for item in self.steps]
        action_ids = [item.action_id for item in self.actions]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("step_id values must be unique")
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("action_id values must be unique")
        if [step.order for step in self.steps] != list(
            range(1, len(self.steps) + 1)
        ):
            raise ValueError("tutorial step order must be contiguous and start at 1")
        if unknown := {item.step_id for item in self.actions} - set(step_ids):
            raise ValueError(f"actions reference unknown tutorial steps: {sorted(unknown)}")
        return self


class TutorialPlanningArtifact(StrictModel):
    """Complete procedure-first planning artifact for tutorial video."""

    planning_type: Literal["tutorial"] = "tutorial"
    one_sentence_thesis: str
    world_state: VideoWorldState
    video_profile: VideoProfileDecision
    objective: str
    materials: list[TutorialMaterial] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    steps: list[TutorialStep] = Field(min_length=1)
    actions: list[TutorialAction] = Field(min_length=1)
    coverage_requirements: list[str] = Field(default_factory=list)

    @classmethod
    def from_parts(
        cls,
        definition: TutorialDefinitionArtifact,
        procedure: TutorialProcedureArtifact,
    ) -> "TutorialPlanningArtifact":
        return cls(
            **definition.model_dump(mode="json"),
            **procedure.model_dump(mode="json"),
        )

    @model_validator(mode="after")
    def validate_tutorial_references(self) -> "TutorialPlanningArtifact":
        material_ids = [item.material_id for item in self.materials]
        step_ids = [item.step_id for item in self.steps]
        action_ids = [item.action_id for item in self.actions]
        for label, values in (
            ("material_id", material_ids),
            ("step_id", step_ids),
            ("action_id", action_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} values must be unique")
        if [step.order for step in self.steps] != list(
            range(1, len(self.steps) + 1)
        ):
            raise ValueError("tutorial step order must be contiguous and start at 1")
        if unknown := {item.step_id for item in self.actions} - set(step_ids):
            raise ValueError(f"actions reference unknown tutorial steps: {sorted(unknown)}")
        return self


NarrativePlanningArtifact = Annotated[
    PlanningArtifact | DramaPlanningArtifact | TutorialPlanningArtifact,
    Field(discriminator="planning_type"),
]


class ScriptArtifact(StrictModel):
    script_type: Literal["explainer"] = "explainer"
    script_version: str = "v1"
    title_options: list[str] = Field(min_length=3, max_length=5)
    segments: list[ScriptSegment] = Field(min_length=6)


class TutorialScriptArtifact(StrictModel):
    """Optional spoken explanations interleaved with visible tutorial actions."""

    script_type: Literal["tutorial"] = "tutorial"
    script_version: str = "v1"
    segments: list[TutorialExplanationSegment] = Field(default_factory=list)


NarrativeScriptArtifact = Annotated[
    ScriptArtifact | TutorialScriptArtifact,
    Field(discriminator="script_type"),
]


class ShotPlanArtifact(StrictModel):
    shots: list[ProductionShot] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_shots(self) -> "ShotPlanArtifact":
        ids = [shot.shot_id for shot in self.shots]
        if len(ids) != len(set(ids)):
            raise ValueError("shot_id values must be unique")
        if [shot.order for shot in self.shots] != list(range(1, len(self.shots) + 1)):
            raise ValueError("shot order must be contiguous and start at 1")
        return self


class NarrativeCandidate(StrictModel):
    """Full uncommitted candidate exported once the task contract is satisfied."""

    world_state: VideoWorldState | None = None
    planning: NarrativePlanningArtifact | None = None
    script: NarrativeScriptArtifact | None = None
    shots: ShotPlanArtifact | None = None

    @model_validator(mode="after")
    def validate_world_state_alignment(self) -> "NarrativeCandidate":
        if (
            self.world_state is not None
            and self.planning is not None
            and self.world_state != self.planning.world_state
        ):
            raise ValueError("candidate world_state does not match planning.world_state")
        if isinstance(self.planning, PlanningArtifact) and self.script is not None:
            if not isinstance(self.script, ScriptArtifact):
                raise ValueError("explainer planning requires an explainer script")
        if isinstance(self.planning, DramaPlanningArtifact) and self.script is not None:
            raise ValueError("drama candidate must use dialogue embedded in scenes")
        if (
            isinstance(self.planning, TutorialPlanningArtifact)
            and self.script is not None
        ):
            if not isinstance(self.script, TutorialScriptArtifact):
                raise ValueError("tutorial planning requires a tutorial script")
            step_ids = {item.step_id for item in self.planning.steps}
            unknown = {
                item.step_id for item in self.script.segments
            } - step_ids
            if unknown:
                raise ValueError(
                    f"tutorial script references unknown steps: {sorted(unknown)}"
                )
        return self


class QualityIssue(StrictModel):
    severity: Literal["error", "warning"]
    code: str
    message: str
    ref: str | None = None


class QualityReport(StrictModel):
    passed: bool
    planned_duration_ms: int
    estimated_script_duration_ms: int
    script_char_count: int
    beat_coverage: float
    evidence_claim_count: int
    issues: list[QualityIssue] = Field(default_factory=list)


class NarrativeArtifact(StrictModel):
    schema_version: str = "narrative.v3"
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    model: str
    brief: CreativeBrief
    planning: NarrativePlanningArtifact
    script: NarrativeScriptArtifact | None = None
    shots: ShotPlanArtifact | None = None
    quality: QualityReport

    @model_validator(mode="after")
    def validate_format_alignment(self) -> "NarrativeArtifact":
        requested = self.brief.production_mode
        actual = self.planning.planning_type
        if requested != "auto" and requested != actual:
            raise ValueError("CreativeBrief production_mode does not match planning_type")
        if actual == "explainer" and not isinstance(self.script, ScriptArtifact):
            raise ValueError("explainer NarrativeArtifact requires explainer script")
        if actual == "drama" and self.script is not None:
            raise ValueError("drama NarrativeArtifact uses embedded scene dialogue")
        if actual == "tutorial" and not isinstance(
            self.script,
            TutorialScriptArtifact,
        ):
            raise ValueError("tutorial NarrativeArtifact requires tutorial script")
        if self.shots is not None and any(
            shot.shot_kind != actual for shot in self.shots.shots
        ):
            raise ValueError("shot_kind does not match planning_type")
        return self
