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
    TutorialShotPayload,
    TutorialVisualSpec,
)
from .models import (
    DramaPlanningArtifact,
    PlanningArtifact,
    ScriptArtifact,
    ShotPlanArtifact,
    TutorialPlanningArtifact,
    TutorialScriptArtifact,
)


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
        shots.append(
            ProductionShot(
                shot_id=f"shot_{action.action_id}",
                order=order,
                shot_kind="tutorial",
                purpose=step.expected_result or step.instruction,
                source_refs=[
                    f"step:{step.step_id}",
                    f"action:{action.action_id}",
                    *(f"explanation:{item}" for item in narration_ids),
                ],
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
        prompt = (
            f"剧情场景 {scene.scene_id}，地点 {scene.location_id}。"
            f"人物：{cast_description}。动作：{action.description}。"
            f"反应：{action.reaction or '自然承接动作'}。对白：{dialogue}。"
            f"镜头：{action.camera_instruction}。"
            f"连续性约束：{'；'.join(continuity) or '保持上一个镜头状态'}。"
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
                    *(f"character:{item}" for item in action.character_ids),
                ],
                world_state_before_ref=f"drama_state:{action.order - 1}",
                world_state_after_ref=f"drama_state:{action.order}",
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
    for index, beat in enumerate(planning.beats, start=1):
        segments = segments_by_beat.get(beat.planned_beat_id)
        if not segments:
            raise ValueError(
                f"beat {beat.planned_beat_id} has no script coverage; "
                "shots cannot be compiled"
            )
        shots.append(
            ProductionShot(
                shot_id=f"shot_{beat.planned_beat_id}",
                order=index,
                shot_kind="explainer",
                purpose=beat.semantic_goal,
                source_refs=[
                    f"planned_beat:{beat.planned_beat_id}",
                    *(
                        f"script_segment:{segment.script_segment_id}"
                        for segment in segments
                    ),
                ],
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
                    narration_text="".join(segment.text for segment in segments),
                    visual_intent=beat.visual_intent_hint,
                ),
            )
        )
    return ShotPlanArtifact(shots=shots)
