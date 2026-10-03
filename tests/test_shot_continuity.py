from __future__ import annotations

import pytest

from typing import Literal

from ugc_harness.content import (
    DramaShotPayload,
    DramaVisualSpec,
    EmbeddedSceneAudioSpec,
    MixedAudioSpec,
    ProductionShot,
    TimingSpec,
    TutorialShotPayload,
    TutorialVisualSpec,
)
from ugc_harness.harness.description import ShotContinuity
from ugc_harness.harness.description_builder import _continuity


def _drama(
    shot_id: str,
    *,
    scene: str,
    location: str,
    characters: list[str],
) -> ProductionShot:
    return ProductionShot(
        shot_id=shot_id,
        order=1,
        shot_kind="drama",
        purpose="推进",
        source_refs=[f"scene:{scene}", "action:a1"],
        binds=[*[f"cast:{item}" for item in characters], f"place:{location}"],
        visual=DramaVisualSpec(
            scene_id=scene,
            character_ids=characters,
            location_id=location,
            action_description="他停住",
            camera_instruction="中景",
            generation_prompt="",
        ),
        audio=EmbeddedSceneAudioSpec(dialogue_lines=[]),
        timing=TimingSpec(duration_driver="generated_clip", target_duration_ms=4000),
        payload=DramaShotPayload(scene_id=scene, action_ids=["a1"]),
    )


def _tutorial(
    shot_id: str,
    *,
    step: str,
    camera: Literal["top_down", "close_up", "medium", "macro"],
    subject: str,
    binds: list[str] | None = None,
) -> ProductionShot:
    return ProductionShot(
        shot_id=shot_id,
        order=1,
        shot_kind="tutorial",
        purpose="演示",
        source_refs=[f"step:{step}", "action:a1"],
        binds=binds or [f"prop:{subject}"],
        visual=TutorialVisualSpec(
            step_id=step,
            action_id="a1",
            subject_ref=subject,
            camera_angle=camera,
            critical_detail="双手把面团揉圆",
        ),
        audio=MixedAudioSpec(
            narration_segment_ids=[],
            preserve_source_audio=True,
            source_audio_types=["tool_sound"],
        ),
        timing=TimingSpec(
            duration_driver="demonstration_action",
            target_duration_ms=4000,
        ),
        payload=TutorialShotPayload(step_id=step, action_ids=["a1"]),
    )


def test_same_scene_place_and_cast_continues() -> None:
    previous = _drama(
        "shot_01",
        scene="scene_kitchen",
        location="location_kitchen",
        characters=["azhou"],
    )
    current = _drama(
        "shot_02",
        scene="scene_kitchen",
        location="location_kitchen",
        characters=["azhou"],
    )
    continuity = _continuity(previous, current)
    assert continuity.join == "continue_from_previous"
    assert continuity.previous_shot_id == "shot_01"


def test_same_scene_but_new_place_and_cast_is_fresh() -> None:
    previous = _drama(
        "shot_04",
        scene="scene_separate",
        location="location_heaven",
        characters=["chang_e"],
    )
    current = _drama(
        "shot_05",
        scene="scene_separate",
        location="location_courtyard",
        characters=["houyi"],
    )
    continuity = _continuity(previous, current)
    assert continuity.join == "fresh"
    assert continuity.previous_shot_id is None


def test_same_scene_same_place_different_cast_bridges() -> None:
    previous = _drama(
        "shot_04",
        scene="scene_separate",
        location="location_heaven",
        characters=["chang_e"],
    )
    current = _drama(
        "shot_05",
        scene="scene_separate",
        location="location_heaven",
        characters=["houyi"],
    )
    continuity = _continuity(previous, current)
    assert continuity.join == "bridge_from_previous"
    assert continuity.previous_shot_id == "shot_04"


def test_scene_switch_same_place_and_cast_bridges() -> None:
    previous = _drama(
        "shot_03",
        scene="scene_attempt",
        location="location_kitchen",
        characters=["azhou"],
    )
    current = _drama(
        "shot_04",
        scene="scene_understanding",
        location="location_kitchen",
        characters=["azhou"],
    )
    continuity = _continuity(previous, current)
    assert continuity.join == "bridge_from_previous"


def test_same_scene_different_place_and_cast_is_fresh() -> None:
    previous = _drama(
        "shot_06",
        scene="scene_regret",
        location="location_palace",
        characters=["chang_e"],
    )
    current = _drama(
        "shot_07",
        scene="scene_regret",
        location="location_courtyard",
        characters=["houyi"],
    )
    continuity = _continuity(previous, current)
    assert continuity.join == "fresh"


def test_tutorial_same_step_same_camera_continues() -> None:
    previous = _tutorial("shot_t1", step="step_fold", camera="close_up", subject="leaf")
    current = _tutorial("shot_t2", step="step_fold", camera="close_up", subject="leaf")
    assert _continuity(previous, current).join == "continue_from_previous"


def test_tutorial_same_step_new_camera_bridges() -> None:
    previous = _tutorial("shot_t1", step="step_fold", camera="close_up", subject="leaf")
    current = _tutorial("shot_t2", step="step_fold", camera="top_down", subject="leaf")
    assert _continuity(previous, current).join == "bridge_from_previous"


def test_tutorial_new_step_shared_workpiece_same_camera_continues() -> None:
    previous = _tutorial("shot_t1", step="step_mix", camera="close_up", subject="flour")
    current = _tutorial(
        "shot_t2",
        step="step_divide",
        camera="close_up",
        subject="flour",
        binds=["prop:flour", "prop:filling"],
    )
    continuity = _continuity(previous, current)
    assert continuity.join == "continue_from_previous"
    assert continuity.previous_shot_id == "shot_t1"


def test_tutorial_new_step_without_overlap_is_fresh() -> None:
    previous = _tutorial("shot_t1", step="step_fold", camera="close_up", subject="leaf")
    current = _tutorial("shot_t2", step="step_fill", camera="close_up", subject="rice")
    assert _continuity(previous, current).join == "fresh"


def test_bridge_requires_previous_shot_id() -> None:
    with pytest.raises(ValueError, match="bridge_from_previous"):
        ShotContinuity(join="bridge_from_previous")


def test_refresh_rewrites_stale_scene_only_continue() -> None:
    from tests.test_description import _drama_artifact
    from ugc_harness.harness.description_builder import (
        build_video_description,
        refresh_shot_continuity,
    )
    from ugc_harness.harness.models import ProjectState

    description = build_video_description(_drama_artifact())
    stale = next(
        shot
        for shot in description.shots
        if shot.continuity.join == "bridge_from_previous"
    )
    previous_id = stale.continuity.previous_shot_id
    stale.continuity = ShotContinuity(
        join="continue_from_previous",
        previous_shot_id=previous_id,
    )
    state = ProjectState()
    state.apply_description(description)
    refresh_shot_continuity(state)
    rewritten = next(
        shot
        for shot in state.production.shots
        if shot.spec.shot_id == stale.spec.shot_id
    )
    assert rewritten.continuity.join == "bridge_from_previous"
    assert rewritten.continuity.previous_shot_id == previous_id
