from ugc_harness.agents.narrative_agent import make_brief
from ugc_harness.agents.narrative_agent.models import (
    DramaPlanningArtifact,
    NarrativeArtifact,
    QualityReport,
    TutorialPlanningArtifact,
)
from ugc_harness.agents.narrative_agent.shots import (
    compile_drama_shots,
    compile_explainer_shots,
    compile_tutorial_shots,
)
from ugc_harness.content import WorldEntity
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
from tests.test_quality import sample_plan, sample_script


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
    production = state.production
    intent = state.creative.intent

    assert intent is not None
    assert intent.format_id == "explainer"
    assert intent.topic == brief.topic
    assert isinstance(production.structure, ExplainerStructure)
    assert production.voice is not None
    assert len(production.voice.utterances) == len(run.artifact.script.segments)
    assert all(
        utterance.anchor_ref.startswith("beat:")
        for utterance in production.voice.utterances
    )
    assert production.shots, "explainer production must carry compiled shots"
    assert all(
        shot.continuity.join == "fresh" and shot.media is None
        for shot in production.shots
    )
    assert production.deliverable is not None
    assert production.deliverable.publish.title_options
    assert state.schema_version == "project-state.v3"
    assert state.creative.brief is not None
    assert state.creative.brief.topic == brief.topic
    assert state.context.project_id
    assert all(
        utterance.speaker_ref is None
        or utterance.speaker_ref.startswith("cast:")
        for utterance in production.voice.utterances
    )

    refs = element_refs(production)
    assert len(refs) == len(set(refs))
    assert set(state.evaluation.elements) == set(refs)
    assert all(
        tag.status == "passed" and tag.version == 1
        for tag in state.evaluation.elements.values()
    )
    graph = state.dependency_graph.nodes
    assert any(ref.startswith("beat:") for ref in graph)
    assert not any(ref.startswith("section:") for ref in graph)
    assert not any(ref.startswith("action:") for ref in graph)
    assert not any(ref.startswith("clip:") for ref in graph)


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
    assert [place.place_id for place in description.world.places] == [
        "location_kitchen"
    ]
    assert isinstance(description.structure, DramaStructure)
    assert description.voice is None, "drama dialogue lives in structure.actions"
    assert all(
        f"cast:character_azhou" in shot.spec.binds
        and f"place:location_kitchen" in shot.spec.binds
        for shot in description.shots
    )
    assert all(
        shot.spec.source_refs
        == [
            f"scene:{shot.spec.payload.scene_id}",
            f"action:{shot.spec.payload.action_ids[0]}",
        ]
        for shot in description.shots
    )

    shots = description.shots
    assert shots[0].continuity.join == "fresh"
    for previous, current in zip(shots, shots[1:]):
        same_scene = (
            previous.spec.payload.scene_id == current.spec.payload.scene_id
        )
        same_place = (
            previous.spec.visual.location_id == current.spec.visual.location_id
        )
        shared_cast = bool(
            set(previous.spec.visual.character_ids)
            & set(current.spec.visual.character_ids)
        )
        if same_scene and same_place and shared_cast:
            assert current.continuity.join == "continue_from_previous"
            assert current.continuity.previous_shot_id == previous.spec.shot_id
        elif same_place or shared_cast:
            assert current.continuity.join == "bridge_from_previous"
            assert current.continuity.previous_shot_id == previous.spec.shot_id
        else:
            assert current.continuity.join == "fresh"
            assert current.continuity.previous_shot_id is None
    scene_ids = {shot.spec.payload.scene_id for shot in shots}
    if len(scene_ids) > 1:
        assert any(
            shot.continuity.join != "continue_from_previous" for shot in shots[1:]
        ), "a scene switch must not hard-continue from the previous clip"


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
        shared = bool(set(previous.spec.binds) & set(current.spec.binds)) or (
            previous.spec.visual.subject_ref == current.spec.visual.subject_ref
        )
        if (same_step or shared) and same_camera:
            assert current.continuity.join == "continue_from_previous"
        elif same_step or shared:
            assert current.continuity.join == "bridge_from_previous"
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
        "prop",
    }
    assert {item.prop_id for item in description.world.props} == {
        "mat_leaf",
        "mat_rice",
        "mat_string",
    }
    assert all(
        any(ref.startswith("prop:") for ref in shot.spec.binds)
        for shot in description.shots
    )
    assert all(
        f"step:{shot.spec.payload.step_id}" in shot.spec.source_refs
        and f"action:{shot.spec.payload.action_ids[0]}" in shot.spec.source_refs
        for shot in description.shots
    )


def test_tutorial_same_name_entity_does_not_duplicate_material() -> None:
    artifact = _tutorial_artifact()
    rice = next(
        item for item in artifact.planning.materials if item.material_id == "mat_rice"
    )
    artifact.planning.world_state.entities.append(
        WorldEntity(
            entity_id="e_rice",
            name=rice.name,
            kind="product",
            narrative_role="原料",
            description="重复登记的糯米",
        )
    )
    artifact.planning.actions[1].description = f"加入{rice.name}并压实"
    artifact.shots = compile_tutorial_shots(artifact.planning, artifact.script)
    description = build_video_description(artifact)
    rice_props = [
        item for item in description.world.props if item.name == rice.name
    ]
    assert [item.prop_id for item in rice_props] == ["mat_rice"]
    fill = next(
        shot for shot in description.shots if shot.spec.shot_id == "shot_action_fill"
    )
    assert "prop:mat_rice" in fill.spec.binds
    assert "prop:e_rice" not in fill.spec.binds


def test_identity_entities_enter_production_world() -> None:
    artifact = _drama_artifact()
    artifact.planning.world_state.entities.append(
        WorldEntity(
            entity_id="prop_zongzi",
            name="四角粽",
            kind="product",
            narrative_role="workpiece",
            description="包好的四角粽",
        )
    )
    description = build_video_description(artifact)
    assert [item.prop_id for item in description.world.props] == ["prop_zongzi"]
    assert {member.character_id for member in description.world.cast} == {
        "character_azhou"
    }
    assert {item.place_id for item in description.world.places} == {"location_kitchen"}

    planning = sample_plan()
    planning.world_state.entities.extend(
        [
            WorldEntity(
                entity_id="product_calendar",
                name="历法",
                kind="product",
                narrative_role="subject",
                description="墙上的年历",
            ),
            WorldEntity(
                entity_id="place_studio",
                name="讲解台",
                kind="place",
                narrative_role="set",
                description="同一张讲解桌",
            ),
        ]
    )
    script = sample_script(planning)
    explainer = NarrativeArtifact(
        model="fake-model",
        brief=make_brief(topic="为什么会有闰年"),
        planning=planning,
        script=script,
        shots=compile_explainer_shots(planning, script),
        quality=_passing_quality(),
    )
    world = build_video_description(explainer).world
    assert {item.prop_id for item in world.props} == {"product_calendar"}
    assert {item.place_id for item in world.places} == {"place_studio"}
    assert "entity_testing" not in {
        *(item.prop_id for item in world.props),
        *(item.place_id for item in world.places),
        *(item.character_id for item in world.cast),
    }


def test_compile_shots_copy_roster_identities_into_binds() -> None:
    artifact = _drama_artifact()
    artifact.planning.world_state.entities.append(
        WorldEntity(
            entity_id="prop_zongzi",
            name="四角粽",
            kind="product",
            narrative_role="workpiece",
            description="包好的四角粽",
        )
    )
    shots = compile_drama_shots(artifact.planning)
    assert all(
        "cast:character_azhou" in shot.binds and "place:location_kitchen" in shot.binds
        for shot in shots.shots
    )
    assert "prop:prop_zongzi" in shots.shots[-1].binds
    assert "prop:prop_zongzi" not in shots.shots[0].binds

    planning = sample_plan()
    planning.world_state.entities.extend(
        [
            WorldEntity(
                entity_id="product_calendar",
                name="历法",
                kind="product",
                narrative_role="subject",
                description="墙上的年历",
            ),
            WorldEntity(
                entity_id="place_studio",
                name="讲解台",
                kind="place",
                narrative_role="set",
                description="同一张讲解桌",
            ),
        ]
    )
    planning.beats[0].bind_refs = ["prop:product_calendar", "place:place_studio"]
    script = sample_script(planning)
    explainer_shots = compile_explainer_shots(planning, script)
    assert explainer_shots.shots[0].binds == [
        "prop:product_calendar",
        "place:place_studio",
    ]
    assert explainer_shots.shots[1].binds == []
