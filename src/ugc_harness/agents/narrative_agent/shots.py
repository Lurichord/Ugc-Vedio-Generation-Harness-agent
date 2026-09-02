"""Deterministic compilation of approved plan and script into ProductionShots.

Compilation is mechanical by design: the creative decisions already live in the
approved PlanningArtifact and ScriptArtifact, so no extra model call is needed.
"""

from __future__ import annotations

from ...content import (
    DramaShotPayload,
    DramaVisualSpec,
    EmbeddedSceneAudioSpec,
    ExplainerShotPayload,
    ExplainerVisualSpec,
    ExternalNarrationAudioSpec,
    MixedAudioSpec,
    ProductionShot,
    ScriptSegment,
    TimingSpec,
    TutorialMaterial,
    TutorialShotPayload,
    TutorialVisualSpec,
    VideoWorldState,
)
from .models import (
    DramaPlanningArtifact,
    PlanningArtifact,
    ScriptArtifact,
    ShotPlanArtifact,
    TutorialPlanningArtifact,
    TutorialScriptArtifact,
)

_KIND_TO_PREFIX = {"person": "cast", "place": "place", "product": "prop"}


def _unique_binds(*groups: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for group in groups:
        for ref in group:
            if ref and ref not in seen:
                seen.add(ref)
                ordered.append(ref)
    return ordered


def _mentioned_binds(
    world_state: VideoWorldState,
    *texts: str,
    materials: list[TutorialMaterial] | None = None,
) -> list[str]:
    blob = "\n".join(text for text in texts if text)
    if not blob:
        return []
    found: list[str] = []
    seen: set[str] = set()
    candidates: list[tuple[str, str, str]] = []
    aroll = world_state.aroll_character
    if aroll is not None:
        candidates.append(
            (aroll.character_id, aroll.character_id, f"cast:{aroll.character_id}")
        )
    for entity in world_state.entities:
        prefix = _KIND_TO_PREFIX.get(entity.kind)
        if prefix is None:
            continue
        ident = entity.entity_id
        if prefix == "prop":
            ident = _canonical_product_id(
                entity.entity_id, entity.name, materials
            )
        candidates.append((entity.entity_id, entity.name, f"{prefix}:{ident}"))
    for entity_id, name, ref in candidates:
        if entity_id in blob or (len(name) >= 2 and name in blob):
            if ref not in seen:
                seen.add(ref)
                found.append(ref)
    return found


def _prop_ref(subject_ref: str) -> str:
    return subject_ref if subject_ref.startswith("prop:") else f"prop:{subject_ref}"


def _canonical_product_id(
    ident: str,
    name: str | None,
    materials: list[TutorialMaterial] | None,
) -> str:
    if not materials:
        return ident
    for item in materials:
        if item.material_id == ident:
            return ident
    if name:
        for item in materials:
            if item.name == name:
                return item.material_id
    return ident


def _subject_prop_ref(
    subject_ref: str,
    planning: TutorialPlanningArtifact,
) -> str:
    ident = subject_ref.partition(":")[2] if ":" in subject_ref else subject_ref
    name = next(
        (
            item.name
            for item in planning.world_state.entities
            if item.entity_id == ident
        ),
        None,
    )
    return _prop_ref(_canonical_product_id(ident, name, planning.materials))


def compile_tutorial_shots(
    planning: TutorialPlanningArtifact,
    script: TutorialScriptArtifact,
) -> ShotPlanArtifact:
    """Compile visible procedure actions; narration remains optional per step."""

    steps = {item.step_id: item for item in planning.steps}
    explanations: dict[str, list] = {}
    for segment in script.segments:
        explanations.setdefault(segment.step_id, []).append(segment)
    shots: list[ProductionShot] = []
    for order, action in enumerate(planning.actions, start=1):
        step = steps[action.step_id]
        narration = explanations.get(action.step_id, [])
        narration_ids = [item.explanation_segment_id for item in narration]
        narration_text = "".join(item.text for item in narration) or None
        subject_ref = action.subject_ref or "tutorial_workpiece"
        critical_detail = action.critical_detail or action.description
        mentioned = [
            f"prop:{item.material_id}"
            for item in planning.materials
            if item.material_id in (action.subject_ref or "")
            or (
                item.name
                and len(item.name) >= 2
                and item.name
                in "\n".join(
                    part
                    for part in (
                        action.description,
                        action.critical_detail,
                        action.subject_ref,
                    )
                    if part
                )
            )
        ]
        shots.append(
            ProductionShot(
                shot_id=f"shot_{action.action_id}",
                order=order,
                shot_kind="tutorial",
                purpose=step.expected_result or step.instruction,
                source_refs=[
                    f"step:{step.step_id}",
                    f"action:{action.action_id}",
                    *(f"utterance:{item}" for item in narration_ids),
                ],
                binds=_unique_binds(
                    (
                        [_subject_prop_ref(action.subject_ref, planning)]
                        if action.subject_ref
                        else []
                    ),
                    mentioned,
                    _mentioned_binds(
                        planning.world_state,
                        action.description,
                        action.critical_detail or "",
                        action.subject_ref or "",
                        step.instruction,
                        materials=planning.materials,
                    ),
                ),
                visual=TutorialVisualSpec(
                    step_id=step.step_id,
                    action_id=action.action_id,
                    subject_ref=subject_ref,
                    camera_angle="close_up" if action.critical_detail else "top_down",
                    critical_detail=critical_detail,
                ),
                audio=MixedAudioSpec(
                    narration_segment_ids=narration_ids,
                    preserve_source_audio=True,
                    source_audio_types=["tool_sound", "material_sound"],
                ),
                timing=TimingSpec(
                    duration_driver="demonstration_action",
                    target_duration_ms=action.target_duration_ms,
                ),
                payload=TutorialShotPayload(
                    step_id=step.step_id,
                    action_ids=[action.action_id],
                    narration_text=narration_text,
                ),
            )
        )
    return ShotPlanArtifact(shots=shots)


def compile_drama_shots(
    planning: DramaPlanningArtifact,
) -> ShotPlanArtifact:
    """Compile each performance action into one audiovisual generation unit."""

    scenes = {item.scene_id: item for item in planning.scenes}
    characters = {item.character_id: item for item in planning.characters}
    shots: list[ProductionShot] = []
    for action in planning.actions:
        scene = scenes[action.scene_id]
        cast = [characters[item] for item in action.character_ids]
        identity_constraints = [
            constraint
            for character in cast
            for constraint in character.appearance_constraints
        ]
        continuity = [*scene.continuity_constraints, *identity_constraints]
        cast_description = "；".join(
            f"{item.name}：{item.description}" for item in cast
        ) or "无出镜人物"
        dialogue = "；".join(action.dialogue_lines) or "无对白"
        binds = _unique_binds(
            [f"cast:{item}" for item in action.character_ids],
            [f"place:{scene.location_id}"] if scene.location_id else [],
            _mentioned_binds(
                planning.world_state,
                action.description,
                action.objective or "",
                action.reaction or "",
                *action.state_changes,
                *action.dialogue_lines,
                scene.purpose,
                *scene.continuity_constraints,
            ),
        )
        prompt = (
            f"剧情场景 {scene.scene_id}，地点 {scene.location_id}。"
            f"人物：{cast_description}。动作：{action.description}。"
            f"反应：{action.reaction or '自然承接动作'}。对白：{dialogue}。"
            f"镜头：{action.camera_instruction}。"
            f"连续性约束：{'；'.join(continuity) or '保持上一个镜头状态'}。"
            f"身份绑定：{'；'.join(binds) or '无'}。"
            "生成画面时同步生成对白、动作声与环境声，不添加外部旁白。"
        )
        shots.append(
            ProductionShot(
                shot_id=f"shot_{action.action_id}",
                order=action.order,
                shot_kind="drama",
                purpose=action.objective or scene.purpose,
                source_refs=[
                    f"scene:{scene.scene_id}",
                    f"action:{action.action_id}",
                ],
                binds=binds,
                visual=DramaVisualSpec(
                    scene_id=scene.scene_id,
                    character_ids=action.character_ids,
                    location_id=scene.location_id,
                    action_description=action.description,
                    camera_instruction=action.camera_instruction,
                    continuity_constraints=continuity,
                    generation_prompt=prompt,
                ),
                audio=EmbeddedSceneAudioSpec(
                    dialogue_lines=action.dialogue_lines,
                    ambient_audio=action.ambient_audio,
                ),
                timing=TimingSpec(
                    duration_driver="generated_clip",
                    target_duration_ms=action.target_duration_ms,
                ),
                payload=DramaShotPayload(
                    scene_id=scene.scene_id,
                    action_ids=[action.action_id],
                    dialogue_lines=action.dialogue_lines,
                ),
            )
        )
    return ShotPlanArtifact(shots=shots)


def compile_explainer_shots(
    planning: PlanningArtifact,
    script: ScriptArtifact,
) -> ShotPlanArtifact:
    segments_by_beat: dict[str, list[ScriptSegment]] = {}
    for segment in script.segments:
        segments_by_beat.setdefault(segment.planned_beat_id, []).append(segment)

    shots: list[ProductionShot] = []
    speaker = (
        planning.video_profile.character_id
        if planning.video_profile.resolved in {"a_roll", "ab_roll"}
        and planning.video_profile.character_id
        else None
    )
    for index, beat in enumerate(planning.beats, start=1):
        segments = segments_by_beat.get(beat.planned_beat_id)
        if not segments:
            raise ValueError(
                f"beat {beat.planned_beat_id} has no script coverage; "
                "shots cannot be compiled"
            )
        narration = "".join(segment.text for segment in segments)
        shots.append(
            ProductionShot(
                shot_id=f"shot_{beat.planned_beat_id}",
                order=index,
                shot_kind="explainer",
                purpose=beat.semantic_goal,
                source_refs=[
                    f"beat:{beat.planned_beat_id}",
                    *(
                        f"utterance:{segment.script_segment_id}"
                        for segment in segments
                    ),
                ],
                binds=_unique_binds(
                    list(beat.bind_refs),
                    [f"cast:{speaker}"] if speaker else [],
                    _mentioned_binds(
                        planning.world_state,
                        beat.visual_intent_hint,
                        beat.semantic_goal,
                        narration,
                    ),
                ),
                visual=ExplainerVisualSpec(
                    visual_source=beat.visual_intent_hint,
                ),
                audio=ExternalNarrationAudioSpec(
                    script_segment_ids=[
                        segment.script_segment_id for segment in segments
                    ],
                    speaker_id=planning.video_profile.character_id,
                ),
                timing=TimingSpec(
                    duration_driver="narration",
                    target_duration_ms=beat.target_duration_ms,
                ),
                payload=ExplainerShotPayload(
                    planned_beat_id=beat.planned_beat_id,
                    script_segment_ids=[
                        segment.script_segment_id for segment in segments
                    ],
                    narration_text=narration,
                    visual_intent=beat.visual_intent_hint,
                ),
            )
        )
    return ShotPlanArtifact(shots=shots)
