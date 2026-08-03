import json
from pathlib import Path

import pytest

from tests.test_narrative_controller import FakeGenerator
from tests.test_quality import sample_plan, sample_script
from ugc_harness.agents.narrative_agent import NarrativeAgent, ScriptArtifact
from ugc_harness.harness.controller import NarrativeHarnessController
from ugc_harness.shared.artifacts import ArtifactWriter
from ugc_harness.agents.narrative_agent import make_brief


def test_controller_runs_constrained_narrative_agent() -> None:
    controller = NarrativeHarnessController.from_generator(
        FakeGenerator(), "fake-model"
    )
    run = controller.run(make_brief(topic="Agent 化测试"))
    artifact = run.artifact

    assert artifact.quality.passed is True
    assert run.record.task.agent == "narrative_agent"
    assert run.record.task.allowed_tools == [
        NarrativeAgent.PLAN_TOOL,
        NarrativeAgent.SCRIPT_TOOL,
    ]
    assert [action.tool for action in run.record.agent_result.actions][0] == (
        NarrativeAgent.PLAN_TOOL
    )
    assert run.record.evaluation.critic_id == "narrative_critic"
    assert artifact.planning.world_state.entities[0].name == "软件测试"
    assert run.record.project_state.video.state_version == 1
    assert run.record.project_state.runtime_context.available_models == {
        "llm": ["fake-model"]
    }
    assert run.record.project_state.video.voice_status == "ready"
    assert run.record.transition.outcome == "advance"
    assert run.record.transition.to_agent == "voice_agent"


def test_failed_review_keeps_control_with_narrative_agent() -> None:
    class CriticFailureGenerator(FakeGenerator):
        def generate(self, prompt: str, output_type: type):
            if output_type is ScriptArtifact:
                script = sample_script(sample_plan())
                script.segments.pop()
                return script
            return super().generate(prompt, output_type)

    run = NarrativeHarnessController.from_generator(
        CriticFailureGenerator(), "fake-model"
    ).run(make_brief(topic="审核失败测试"))

    assert run.record.evaluation.passed is False
    assert run.record.transition.outcome == "revise"
    assert run.record.transition.to_agent == "narrative_agent"
    assert run.record.project_state.video.narrative_status == "needs_revision"
    assert run.record.project_state.video.voice_status == "blocked"


def test_narrative_revision_preserves_both_tasks() -> None:
    class CriticFailureGenerator(FakeGenerator):
        def generate(self, prompt: str, output_type: type):
            if output_type is ScriptArtifact:
                script = sample_script(sample_plan())
                script.segments.pop()
                return script
            return super().generate(prompt, output_type)

    brief = make_brief(topic="叙事修订历史测试")
    rejected = NarrativeHarnessController.from_generator(
        CriticFailureGenerator(), "fake-model"
    ).run(brief)
    revised = NarrativeHarnessController.from_generator(
        FakeGenerator(), "fake-model"
    ).run(brief, state=rejected.record.project_state)

    tasks = revised.record.project_state.trajectory.phases["narrative"].tasks
    assert len(tasks) == 2
    assert [task.task_kind for task in tasks] == ["generation", "revision"]
    assert tasks[0].graph_update.committed is False
    assert tasks[1].graph_update.committed is True
    assert revised.record.project_state.video.narrative_status == "passed"
    assert revised.record.project_state.video.voice_status == "ready"


def test_controller_rejects_task_based_on_stale_state() -> None:
    controller = NarrativeHarnessController.from_generator(
        FakeGenerator(), "fake-model", state_version=2
    )
    brief = make_brief(topic="状态版本测试")
    task = controller.create_task(brief).model_copy(
        update={"based_on_state_version": 1}
    )

    with pytest.raises(ValueError, match="STALE_RESULT"):
        controller.run(brief, task)


def test_explicit_video_profile_cannot_be_overridden_by_ai() -> None:
    run = NarrativeHarnessController.from_generator(
        FakeGenerator(), "fake-model"
    ).run(make_brief(topic="人物口播测试", video_profile="ab_roll"))

    assert run.record.evaluation.passed is False
    assert any(
        issue.code == "VIDEO_PROFILE_MISMATCH"
        for issue in run.record.evaluation.issues
    )
    assert run.record.transition.outcome == "revise"


def test_writer_persists_task_result_critic_and_state(tmp_path: Path) -> None:
    run = NarrativeHarnessController.from_generator(
        FakeGenerator(), "fake-model"
    ).run(make_brief(topic="轨迹落盘测试"))
    artifact = run.artifact
    writer = ArtifactWriter(tmp_path)
    project_dir, _ = writer.write(artifact)

    written = writer.write_narrative_run(project_dir, run.record)

    assert {path.name for path in written} == {
        "narrative_task.json",
        "narrative_agent_result.json",
        "narrative_evaluation.json",
        "narrative_transition.json",
        "project_state.json",
        "manifest.json",
    }
    state = json.loads(
        (project_dir / "harness" / "project_state.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["runtime_context"]["available_tools"]
    assert "world" not in state
    assert state["world_state"]["topic_frame"] == "解释测试为什么重要"
    tasks = state["trajectory"]["phases"]["narrative"]["tasks"]
    assert tasks[0]["agent_result"]["actions"]
    assert tasks[0]["transition"]["to_agent"] == "voice_agent"
    assert tasks[0]["task_kind"] == "generation"
    assert tasks[0]["graph_update"]["committed"] is True
    manifest = json.loads(
        (project_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["stage"] == "narrative_agent_complete"
    assert manifest["state_version"] == 1
