from ugc_harness.agents.narrative_agent import make_brief
from ugc_harness.agents.narrative_agent.models import (
    DramaPlanningArtifact,
    NarrativeArtifact,
    QualityReport,
    TutorialPlanningArtifact,
)
from ugc_harness.agents.narrative_agent.shots import (
    compile_drama_shots,
    compile_tutorial_shots,
)
from ugc_harness.harness.description import (
    DramaStructure,
    ExplainerStructure,
    TutorialStructure,
    element_refs,
)
from ugc_harness.harness.description_builder import (
    build_video_description,
    initial_execution_state,
)

from tests.fixtures.narrative_mcp_server import (
    _sample_drama_actions,
    _sample_drama_story,
    _sample_drama_world,
    _sample_tutorial_definition,
    _sample_tutorial_procedure,
    plan_tutorial_explanations,
)
from tests.test_narrative_controller import (
    FakeGenerator,
    narrative_controller_from_generator,
)


def _passing_quality() -> QualityReport:
    return QualityReport(
        passed=True,
        planned_duration_ms=90_000,
        estimated_script_duration_ms=0,
        script_char_count=0,
        beat_coverage=1.0,
        evidence_claim_count=0,
    )


def _drama_artifact() -> NarrativeArtifact:
    planning = DramaPlanningArtifact.from_parts(
        _sample_drama_world(),
        _sample_drama_story(),
        _sample_drama_actions(),
    )
    return NarrativeArtifact(
        model="fake-model",
        brief=make_brief(topic="端午节的粽子", production_mode="drama"),
        planning=planning,
        shots=compile_drama_shots(planning),
        quality=_passing_quality(),
    )


def _tutorial_artifact() -> NarrativeArtifact:
    definition = _sample_tutorial_definition()
    procedure = _sample_tutorial_procedure()
    planning = TutorialPlanningArtifact.from_parts(definition, procedure)
    import tests.fixtures.narrative_mcp_server as fixture

    fixture._tutorial_definition = definition
    fixture._tutorial_procedure = procedure
    script = plan_tutorial_explanations()
    return NarrativeArtifact(
        model="fake-model",
        brief=make_brief(topic="包粽子教程", production_mode="tutorial"),
        planning=planning,
        script=script,
        shots=compile_tutorial_shots(planning, script),
        quality=_passing_quality(),
    )


def test_explainer_controller_commits_description() -> None:
    brief = make_brief(topic="为什么会有闰年", duration_seconds=90)
    run = narrative_controller_from_generator(FakeGenerator(), "fake-model").run(
        brief
    )
    state = run.record.project_state
    description = state.description

    assert description is not None
    assert description.intent.format_id == "explainer"
    assert description.intent.topic == brief.topic
    assert isinstance(description.structure, ExplainerStructure)
    assert description.voice is not None
    assert len(description.voice.utterances) == len(run.artifact.script.segments)
    assert all(
        utterance.anchor_ref.startswith("beat:")
        for utterance in description.voice.utterances
    )
    assert description.shots, "explainer description must carry compiled shots"
    assert all(
        shot.continuity.join == "fresh" and shot.media is None
        for shot in description.shots
    )
    assert description.deliverable is not None
    assert description.deliverable.publish.title_options

    refs = element_refs(description)
    assert len(refs) == len(set(refs))
    assert set(state.execution.elements) == set(refs)
    assert all(
        tag.status == "passed" and tag.version == 1
        for tag in state.execution.elements.values()
    )


def test_drama_description_cast_structure_and_continuity() -> None:
    artifact = _drama_artifact()
    description = build_video_description(artifact)

    assert description.intent.format_id == "drama"
    assert description.intent.promise == artifact.planning.premise
    performers = [
        member for member in description.world.cast if member.role == "performer"
    ]
    assert [member.character_id for member in performers] == ["character_azhou"]
    assert performers[0].appearance_constraints == ["白色棉麻衬衫", "短发"]
    assert isinstance(description.structure, DramaStructure)
    assert description.voice is None, "drama dialogue lives in structure.actions"

    shots = description.shots
    assert shots[0].continuity.join == "fresh"
    for previous, current in zip(shots, shots[1:]):
        same_scene = (
            previous.spec.payload.scene_id == current.spec.payload.scene_id
        )
        if same_scene:
            assert current.continuity.join == "continue_from_previous"
            assert current.continuity.previous_shot_id == previous.spec.shot_id
        else:
            assert current.continuity.join == "fresh"
            assert current.continuity.previous_shot_id is None
    scene_ids = {shot.spec.payload.scene_id for shot in shots}
    if len(scene_ids) > 1:
        assert any(
            shot.continuity.join == "fresh" for shot in shots[1:]
        ), "a scene switch must restart generation fresh"


def test_tutorial_description_result_utterances_and_continuity() -> None:
    artifact = _tutorial_artifact()
    description = build_video_description(artifact)

    assert isinstance(description.structure, TutorialStructure)
    assert description.structure.result.success_criteria == list(
        artifact.planning.coverage_requirements
    )
    assert description.voice is not None
    assert all(
        utterance.anchor_ref.startswith("step:")
        for utterance in description.voice.utterances
    )
    placements = {u.placement for u in description.voice.utterances}
    assert placements <= {"before_action", "during_action", "after_action"}

    shots = description.shots
    assert shots[0].continuity.join == "fresh"
    for previous, current in zip(shots, shots[1:]):
        same_step = previous.spec.payload.step_id == current.spec.payload.step_id
        same_camera = (
            previous.spec.visual.camera_angle == current.spec.visual.camera_angle
        )
        if same_step and same_camera:
            assert current.continuity.join == "continue_from_previous"
        else:
            assert current.continuity.join == "fresh"

    execution = initial_execution_state(description)
    refs = element_refs(description)
    assert set(execution.elements) == set(refs)
    assert {ref.split(":", 1)[0] for ref in refs} >= {
        "step",
        "action",
        "utterance",
        "shot",
    }
