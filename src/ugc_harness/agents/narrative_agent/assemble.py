"""Assemble a narrative candidate from authored layers and ProductionShots.

The agent writes one layer at a time. Submit uses those layers when present
and fills any missing planning fields from the shots.
"""

from __future__ import annotations

from typing import Any

from ...content import (
    AudienceDelta,
    DeliveryHint,
    DramaAction,
    DramaCharacter,
    DramaScene,
    DramaShotPayload,
    DramaVisualSpec,
    EvidenceNeed,
    ExplainerShotPayload,
    NarrativeFormatId,
    PlannedBeat,
    ProductionShot,
    ScriptSegment,
    Section,
    TutorialAction,
    TutorialExplanationSegment,
    TutorialMaterial,
    TutorialShotPayload,
    TutorialStep,
    TutorialVisualSpec,
    VideoWorldState,
    identity_refs_for_world,
)
from ...profiles.models import VideoProfileDecision
from .models import (
    BeatPlanArtifact,
    CreativeBrief,
    DramaActionPlanArtifact,
    DramaPlanningArtifact,
    DramaStoryArtifact,
    DramaWorldArtifact,
    NarrativeCandidate,
    NarrativeIdentitiesArtifact,
    PlanningArtifact,
    ScriptArtifact,
    SectionPlanArtifact,
    ShotPlanArtifact,
    TutorialDefinitionArtifact,
    TutorialPlanningArtifact,
    TutorialProcedureArtifact,
    TutorialScriptArtifact,
)

_BEAT_ROLES: list[tuple[str, str]] = [
    ("question", "opening"),
    ("reveal", "contrast"),
    ("claim", "continuation"),
    ("evidence", "evidence_for"),
    ("explanation", "cause"),
    ("implication", "escalation"),
    ("payoff", "resolution"),
    ("callback", "callback"),
]


def resolve_video_profile(
    brief: CreativeBrief,
    format_id: str,
    world: VideoWorldState,
    existing: VideoProfileDecision | None = None,
) -> VideoProfileDecision:
    if existing is not None:
        return existing
    aroll = world.aroll_character
    if brief.video_profile in {"a_roll", "ab_roll"}:
        if aroll is None:
            raise ValueError("speaker-led profile 需要 world_state.aroll_character")
        return VideoProfileDecision(
            requested=brief.video_profile,
            resolved=brief.video_profile,
            selection_source="user",
            rationale="用户指定口播形象",
            speaker_presence_ratio_min=0.35 if brief.video_profile == "ab_roll" else 0.5,
            speaker_presence_ratio_max=0.9,
            character_consistency_required=True,
            character_id=aroll.character_id,
            character_description=aroll.visual_description,
        )
    requested = brief.video_profile
    if format_id == "drama" and requested not in {"auto", "b_roll"}:
        requested = "auto"
    return VideoProfileDecision(
        requested=requested,
        resolved="b_roll",
        selection_source="user" if requested == "b_roll" else "ai",
        rationale="短剧无主持人口播" if format_id == "drama" else "默认画外说明",
        speaker_presence_ratio_min=0.0,
        speaker_presence_ratio_max=0.0 if format_id == "drama" else 0.15,
        character_consistency_required=False,
    )


def with_resolved_profile(
    identities: NarrativeIdentitiesArtifact,
    brief: CreativeBrief,
    format_id: str,
) -> NarrativeIdentitiesArtifact:
    profile = resolve_video_profile(
        brief, format_id, identities.world_state, identities.video_profile
    )
    if identities.video_profile == profile:
        return identities
    return identities.model_copy(update={"video_profile": profile})


def fill_generation_prompt(shot: ProductionShot) -> ProductionShot:
    """Keep the authored director card. Asset writes the Seedance prompt."""

    return shot


def assemble_candidate(
    *,
    brief: CreativeBrief,
    format_id: NarrativeFormatId,
    identities: NarrativeIdentitiesArtifact,
    shots: ShotPlanArtifact,
    layers: dict[str, Any] | None = None,
) -> NarrativeCandidate:
    filled = ShotPlanArtifact(
        shots=[fill_generation_prompt(item) for item in shots.shots]
    )
    book = with_resolved_profile(identities, brief, format_id)
    parts = layers or {}
    if format_id == "drama":
        planning = _drama_from_layers(book, filled, parts)
        return NarrativeCandidate(
            world_state=planning.world_state,
            planning=planning,
            shots=filled,
        )
    if format_id == "tutorial":
        planning, script = _tutorial_from_layers(book, filled, parts)
        return NarrativeCandidate(
            world_state=planning.world_state,
            planning=planning,
            script=script,
            shots=filled,
        )
    planning, script = _explainer_from_layers(book, filled, parts)
    return NarrativeCandidate(
        world_state=planning.world_state,
        planning=planning,
        script=script,
        shots=filled,
    )


def _explainer_from_layers(
    book: NarrativeIdentitiesArtifact,
    shots: ShotPlanArtifact,
    layers: dict[str, Any],
) -> tuple[PlanningArtifact, ScriptArtifact]:
    inferred, inferred_script = _explainer_planning(book, shots)
    section_plan = layers.get("section_plan")
    beat_plan = layers.get("beat_plan")
    script = layers.get("script")
    if isinstance(section_plan, SectionPlanArtifact) and isinstance(
        beat_plan, BeatPlanArtifact
    ):
        planning = PlanningArtifact.from_parts(section_plan, beat_plan)
    elif isinstance(section_plan, SectionPlanArtifact):
        planning = inferred.model_copy(
            update={
                "sections": section_plan.sections,
                "narrative_pattern": section_plan.narrative_pattern,
                "one_sentence_thesis": section_plan.one_sentence_thesis,
                "world_state": section_plan.world_state,
                "video_profile": section_plan.video_profile,
            }
        )
    else:
        planning = inferred
    if isinstance(script, ScriptArtifact):
        return planning, script
    return planning, inferred_script


def _drama_from_layers(
    book: NarrativeIdentitiesArtifact,
    shots: ShotPlanArtifact,
    layers: dict[str, Any],
) -> DramaPlanningArtifact:
    inferred = _drama_planning(book, shots)
    world = layers.get("world")
    story = layers.get("story")
    action_plan = layers.get("action_plan")
    if (
        isinstance(world, DramaWorldArtifact)
        and isinstance(story, DramaStoryArtifact)
        and isinstance(action_plan, DramaActionPlanArtifact)
    ):
        return DramaPlanningArtifact.from_parts(world, story, action_plan)
    updates: dict[str, Any] = {}
    if isinstance(world, DramaWorldArtifact):
        updates.update(
            {
                "one_sentence_thesis": world.one_sentence_thesis,
                "world_state": world.world_state,
                "video_profile": world.video_profile,
                "characters": world.characters,
            }
        )
    if isinstance(story, DramaStoryArtifact):
        updates["premise"] = story.premise
        updates["scenes"] = story.scenes
    if isinstance(action_plan, DramaActionPlanArtifact):
        updates["actions"] = action_plan.actions
    if updates:
        return inferred.model_copy(update=updates)
    return inferred


def _tutorial_from_layers(
    book: NarrativeIdentitiesArtifact,
    shots: ShotPlanArtifact,
    layers: dict[str, Any],
) -> tuple[TutorialPlanningArtifact, TutorialScriptArtifact]:
    inferred, inferred_script = _tutorial_planning(book, shots)
    definition = layers.get("definition")
    procedure = layers.get("procedure")
    script = layers.get("script")
    if isinstance(definition, TutorialDefinitionArtifact) and isinstance(
        procedure, TutorialProcedureArtifact
    ):
        planning = TutorialPlanningArtifact.from_parts(definition, procedure)
    else:
        updates: dict[str, Any] = {}
        if isinstance(definition, TutorialDefinitionArtifact):
            updates.update(
                {
                    "one_sentence_thesis": definition.one_sentence_thesis,
                    "world_state": definition.world_state,
                    "video_profile": definition.video_profile,
                    "objective": definition.objective,
                    "materials": definition.materials,
                    "tools": definition.tools,
                    "coverage_requirements": definition.coverage_requirements,
                }
            )
        if isinstance(procedure, TutorialProcedureArtifact):
            updates["steps"] = procedure.steps
            updates["actions"] = procedure.actions
        planning = inferred.model_copy(update=updates) if updates else inferred
    if isinstance(script, TutorialScriptArtifact):
        return planning, script
    return planning, inferred_script


def _drama_characters(book: NarrativeIdentitiesArtifact) -> list[DramaCharacter]:
    if book.characters:
        return list(book.characters)
    found = [
        DramaCharacter(
            character_id=item.entity_id,
            name=item.name,
            description=item.description,
        )
        for item in book.world_state.entities
        if item.kind == "person"
    ]
    if not found:
        raise ValueError("短剧身份册里至少要有一个 person")
    return found


def _drama_planning(
    book: NarrativeIdentitiesArtifact,
    shots: ShotPlanArtifact,
) -> DramaPlanningArtifact:
    grouped: dict[str, list[ProductionShot]] = {}
    for shot in shots.shots:
        grouped.setdefault(_scene_id(shot), []).append(shot)
    if len(grouped) < 2:
        raise ValueError("短剧至少两场，请给镜头写不同的 scene_id")
    if len(shots.shots) < 4:
        raise ValueError("短剧至少四镜")
    scenes: list[DramaScene] = []
    actions: list[DramaAction] = []
    for order, (scene_id, scene_shots) in enumerate(
        sorted(grouped.items(), key=lambda item: item[1][0].order),
        start=1,
    ):
        first = scene_shots[0]
        location = _location_id(first)
        characters = _character_ids(first)
        for item in scene_shots[1:]:
            for ident in _character_ids(item):
                if ident not in characters:
                    characters.append(ident)
        continuity: list[str] = []
        for item in scene_shots:
            visual = item.visual
            if isinstance(visual, DramaVisualSpec):
                for line in visual.continuity_constraints:
                    if line not in continuity:
                        continuity.append(line)
        scenes.append(
            DramaScene(
                scene_id=scene_id,
                order=order,
                location_id=location,
                purpose=first.purpose,
                character_ids=characters,
                continuity_constraints=continuity,
            )
        )
        for shot in scene_shots:
            visual = shot.visual
            description = (
                visual.action_description
                if isinstance(visual, DramaVisualSpec)
                else shot.purpose
            )
            camera = (
                visual.camera_instruction
                if isinstance(visual, DramaVisualSpec)
                else "中景，机位固定"
            )
            dialogue = list(getattr(shot.audio, "dialogue_lines", []) or [])
            if isinstance(shot.payload, DramaShotPayload) and shot.payload.dialogue_lines:
                dialogue = list(shot.payload.dialogue_lines)
            actions.append(
                DramaAction(
                    action_id=_action_id(shot),
                    order=len(actions) + 1,
                    scene_id=scene_id,
                    description=description,
                    character_ids=_character_ids(shot) or characters,
                    objective=shot.purpose,
                    dialogue_lines=dialogue,
                    camera_instruction=camera,
                    ambient_audio=getattr(shot.audio, "ambient_audio", None),
                    target_duration_ms=shot.timing.target_duration_ms or 5000,
                )
            )
    assert book.video_profile is not None
    return DramaPlanningArtifact(
        one_sentence_thesis=book.one_sentence_thesis,
        world_state=book.world_state,
        video_profile=book.video_profile,
        premise=book.one_sentence_thesis,
        characters=_drama_characters(book),
        scenes=scenes,
        actions=actions,
    )


def _explainer_planning(
    book: NarrativeIdentitiesArtifact,
    shots: ShotPlanArtifact,
) -> tuple[PlanningArtifact, ScriptArtifact]:
    if len(shots.shots) < 6:
        raise ValueError("讲解至少六镜")
    total = len(shots.shots)
    hook_end = max(1, total // 4)
    close_start = total - max(1, total // 4)
    sections = [
        Section(
            section_id="section_hook",
            role="hook",
            target_duration_ms=15_000,
            goal="建立信息缺口",
            attention_strategy="问题或反差",
        ),
        Section(
            section_id="section_body",
            role="body",
            target_duration_ms=55_000,
            goal="解释与证据",
            attention_strategy="推进论证",
        ),
        Section(
            section_id="section_close",
            role="close",
            target_duration_ms=20_000,
            goal="兑现开头",
            attention_strategy="收束",
        ),
    ]
    beats: list[PlannedBeat] = []
    segments: list[ScriptSegment] = []
    roster = identity_refs_for_world(book.world_state)
    for index, shot in enumerate(shots.shots, start=1):
        role, relation = _BEAT_ROLES[(index - 1) % len(_BEAT_ROLES)]
        if index <= hook_end:
            section_id = "section_hook"
        elif index > close_start:
            section_id = "section_close"
        else:
            section_id = "section_body"
        beat_id = (
            shot.payload.planned_beat_id
            if isinstance(shot.payload, ExplainerShotPayload)
            else f"beat_{shot.shot_id}"
        )
        visual_intent = (
            shot.payload.visual_intent
            if isinstance(shot.payload, ExplainerShotPayload)
            else getattr(shot.visual, "visual_source", shot.purpose)
        )
        binds = [ref for ref in shot.binds if ref in roster] or list(shot.binds)
        duration = shot.timing.target_duration_ms or 4000
        duration = min(15_000, max(1_500, duration))
        beats.append(
            PlannedBeat(
                planned_beat_id=beat_id,
                section_id=section_id,
                order=index,
                semantic_goal=shot.purpose,
                discourse_role=role,  # type: ignore[arg-type]
                relation_to_previous=relation,  # type: ignore[arg-type]
                target_effect=shot.purpose,
                target_duration_ms=duration,
                audience_delta=AudienceDelta(
                    knowledge_added=[shot.purpose],
                    emotion_target="清楚",
                ),
                evidence_need=EvidenceNeed(required=False),
                visual_intent_hint=visual_intent,
                bind_refs=binds,
            )
        )
        narration = (
            shot.payload.narration_text
            if isinstance(shot.payload, ExplainerShotPayload)
            else shot.purpose
        )
        if len(narration.strip()) < 2:
            narration = shot.purpose
        segment_id = (
            shot.payload.script_segment_ids[0]
            if isinstance(shot.payload, ExplainerShotPayload)
            and shot.payload.script_segment_ids
            else f"seg_{shot.shot_id}"
        )
        segments.append(
            ScriptSegment(
                script_segment_id=segment_id,
                planned_beat_id=beat_id,
                text=narration,
                delivery_hint=DeliveryHint(speech_act=role),  # type: ignore[arg-type]
            )
        )
    assert book.video_profile is not None
    topic = book.one_sentence_thesis
    titles = [topic[:20] or "主题", f"{topic[:12]}说明", f"关于{topic[:12]}"]
    planning = PlanningArtifact(
        narrative_pattern="Question→Evidence→Explanation→Implication",
        one_sentence_thesis=book.one_sentence_thesis,
        world_state=book.world_state,
        video_profile=book.video_profile,
        sections=sections,
        beats=beats,
    )
    script = ScriptArtifact(title_options=titles, segments=segments)
    return planning, script


def _tutorial_planning(
    book: NarrativeIdentitiesArtifact,
    shots: ShotPlanArtifact,
) -> tuple[TutorialPlanningArtifact, TutorialScriptArtifact]:
    materials = list(book.materials)
    if not materials:
        materials = [
            TutorialMaterial(
                material_id=item.entity_id,
                name=item.name,
                quantity=None,
            )
            for item in book.world_state.entities
            if item.kind == "product"
        ]
    steps: list[TutorialStep] = []
    seen: dict[str, int] = {}
    for shot in shots.shots:
        step_id = _step_id(shot)
        if step_id in seen:
            continue
        seen[step_id] = len(steps) + 1
        detail = ""
        if isinstance(shot.visual, TutorialVisualSpec):
            detail = shot.visual.critical_detail
        steps.append(
            TutorialStep(
                step_id=step_id,
                order=seen[step_id],
                instruction=shot.purpose,
                visual_evidence=[detail or shot.purpose],
            )
        )
    actions: list[TutorialAction] = []
    segments: list[TutorialExplanationSegment] = []
    for shot in shots.shots:
        visual = shot.visual
        actions.append(
            TutorialAction(
                action_id=_action_id(shot),
                step_id=_step_id(shot),
                description=(
                    visual.critical_detail
                    if isinstance(visual, TutorialVisualSpec)
                    else shot.purpose
                ),
                subject_ref=(
                    visual.subject_ref
                    if isinstance(visual, TutorialVisualSpec)
                    else None
                ),
                critical_detail=(
                    visual.critical_detail
                    if isinstance(visual, TutorialVisualSpec)
                    else None
                ),
                target_duration_ms=shot.timing.target_duration_ms or 8000,
            )
        )
        text = (
            shot.payload.narration_text
            if isinstance(shot.payload, TutorialShotPayload)
            else None
        )
        spoken = text if text and len(text.strip()) >= 2 else None
        ids = list(getattr(shot.audio, "narration_segment_ids", []) or [])
        if ids:
            for ident in ids:
                segments.append(
                    TutorialExplanationSegment(
                        explanation_segment_id=ident,
                        step_id=_step_id(shot),
                        placement="during_action",
                        text=spoken or shot.purpose,
                    )
                )
        elif spoken:
            segments.append(
                TutorialExplanationSegment(
                    explanation_segment_id=f"te_{shot.shot_id}",
                    step_id=_step_id(shot),
                    placement="during_action",
                    text=spoken,
                )
            )
    assert book.video_profile is not None
    planning = TutorialPlanningArtifact(
        one_sentence_thesis=book.one_sentence_thesis,
        world_state=book.world_state,
        video_profile=book.video_profile,
        objective=book.objective or book.one_sentence_thesis,
        materials=materials,
        tools=list(book.tools),
        steps=steps,
        actions=actions,
        coverage_requirements=list(book.coverage_requirements),
    )
    return planning, TutorialScriptArtifact(segments=segments)


def _scene_id(shot: ProductionShot) -> str:
    if isinstance(shot.payload, DramaShotPayload):
        return shot.payload.scene_id
    if isinstance(shot.visual, DramaVisualSpec):
        return shot.visual.scene_id
    return f"scene_{shot.shot_id}"


def _location_id(shot: ProductionShot) -> str:
    if isinstance(shot.visual, DramaVisualSpec):
        return shot.visual.location_id
    for ref in shot.binds:
        if ref.startswith("place:"):
            return ref.partition(":")[2]
    return "location_unknown"


def _character_ids(shot: ProductionShot) -> list[str]:
    if isinstance(shot.visual, DramaVisualSpec) and shot.visual.character_ids:
        return list(shot.visual.character_ids)
    found: list[str] = []
    for ref in shot.binds:
        if ref.startswith("cast:"):
            found.append(ref.partition(":")[2])
    return found


def _action_id(shot: ProductionShot) -> str:
    if isinstance(shot.payload, DramaShotPayload) and shot.payload.action_ids:
        return shot.payload.action_ids[0]
    if isinstance(shot.payload, TutorialShotPayload) and shot.payload.action_ids:
        return shot.payload.action_ids[0]
    return f"act_{shot.shot_id}"


def _step_id(shot: ProductionShot) -> str:
    if isinstance(shot.payload, TutorialShotPayload):
        return shot.payload.step_id
    if isinstance(shot.visual, TutorialVisualSpec):
        return shot.visual.step_id
    return f"step_{shot.shot_id}"
