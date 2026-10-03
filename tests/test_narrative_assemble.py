from ugc_harness.agents.narrative_agent import make_brief
from ugc_harness.agents.narrative_agent.assemble import assemble_candidate
from ugc_harness.agents.narrative_agent.models import (
    DramaPlanningArtifact,
    NarrativeIdentitiesArtifact,
    PlanningArtifact,
    TutorialPlanningArtifact,
    TutorialScriptArtifact,
)
from ugc_harness.agents.narrative_agent.shots import (
    compile_drama_shots,
    compile_explainer_shots,
    compile_tutorial_shots,
)
from ugc_harness.evaluators.narrative_critic import NarrativeCritic
from tests.fixtures.narrative_mcp_server import (
    _canned_identities,
    _sample_drama_actions,
    _sample_drama_story,
    _sample_drama_world,
    _sample_tutorial_definition,
    _sample_tutorial_procedure,
)
from tests.test_quality import sample_plan, sample_script


def test_assemble_explainer_from_shots_passes_critic() -> None:
    plan = sample_plan()
    script = sample_script(plan)
    shots = compile_explainer_shots(plan, script)
    book = NarrativeIdentitiesArtifact(
        one_sentence_thesis=plan.one_sentence_thesis,
        world_state=plan.world_state,
        video_profile=plan.video_profile,
    )
    brief = make_brief(topic="测试主题", duration_seconds=90)
    candidate = assemble_candidate(
        brief=brief,
        format_id="explainer",
        identities=book,
        shots=shots,
    )
    assert isinstance(candidate.planning, PlanningArtifact)
    quality, evaluation = NarrativeCritic().evaluate(
        brief,
        candidate.planning,
        candidate.script,
        "narrative:test",
        shots=candidate.shots,
    )
    assert evaluation.passed is True
    assert quality.passed is True


def test_assemble_drama_from_shots_keeps_two_scenes() -> None:
    world = _sample_drama_world()
    planning = DramaPlanningArtifact.from_parts(
        world, _sample_drama_story(), _sample_drama_actions()
    )
    shots = compile_drama_shots(planning)
    book = _canned_identities("drama")
    brief = make_brief(topic="端午节旧粽叶", production_mode="drama")
    candidate = assemble_candidate(
        brief=brief,
        format_id="drama",
        identities=book,
        shots=shots,
    )
    assert isinstance(candidate.planning, DramaPlanningArtifact)
    assert len(candidate.planning.scenes) == 2
    assert candidate.script is None
    _, evaluation = NarrativeCritic().evaluate(
        brief,
        candidate.planning,
        None,
        "narrative:test",
        shots=candidate.shots,
    )
    assert evaluation.passed is True


def test_assemble_tutorial_from_shots_covers_steps() -> None:
    definition = _sample_tutorial_definition()
    procedure = _sample_tutorial_procedure()
    planning = TutorialPlanningArtifact.from_parts(definition, procedure)
    script = TutorialScriptArtifact.model_validate(
        {
            "segments": [
                {
                    "explanation_segment_id": "te01",
                    "step_id": "step_fold",
                    "placement": "during_action",
                    "text": "底部折角要完全闭合。",
                }
            ]
        }
    )
    shots = compile_tutorial_shots(planning, script)
    book = _canned_identities("tutorial")
    brief = make_brief(topic="端午节四角粽制作", production_mode="tutorial")
    candidate = assemble_candidate(
        brief=brief,
        format_id="tutorial",
        identities=book,
        shots=shots,
    )
    assert isinstance(candidate.planning, TutorialPlanningArtifact)
    assert {step.step_id for step in candidate.planning.steps} == {
        "step_fold",
        "step_fill",
        "step_tie",
    }
    _, evaluation = NarrativeCritic().evaluate(
        brief,
        candidate.planning,
        candidate.script,
        "narrative:test",
        shots=candidate.shots,
    )
    assert evaluation.passed is True
