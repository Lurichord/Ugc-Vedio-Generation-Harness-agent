from typing import TypeVar

from pydantic import BaseModel

from ugc_harness.agents.narrative_agent import (
    PlanningArtifact,
    ScriptArtifact,
    make_brief,
)
from ugc_harness.agents.generic import GenericAgentExecution
from ugc_harness.agents.narrative_agent.models import NarrativeCandidate
from ugc_harness.agents.narrative_agent.shots import compile_explainer_shots
from ugc_harness.harness.narrative_formats import EXPLAINER_PACK
from ugc_harness.evaluators.narrative_critic import NarrativeCritic
from ugc_harness.harness.controller import NarrativeHarnessController
from ugc_harness.harness.models import (
    ActionRecord,
    AgentResult,
    ArtifactRef,
    StatePatch,
    TaskEnvelope,
)
from ugc_harness.tools.registry import ToolRegistry
from tests.test_quality import sample_plan, sample_script

T = TypeVar("T", bound=BaseModel)


class FakeGenerator:
    settings = object()

    def generate(self, prompt: str, output_type: type[T]) -> T:
        if output_type is PlanningArtifact:
            return sample_plan()  # type: ignore[return-value]
        if output_type is ScriptArtifact:
            return sample_script(sample_plan())  # type: ignore[return-value]
        raise AssertionError(f"Unexpected output type: {output_type}")


class _GeneratedNarrativeAgent:
    """Test-only controller seam; production Narrative always runs through MCP."""

    name = "narrative_agent"

    def __init__(self, generator: object) -> None:
        self.generator = generator
        self.tools = ToolRegistry()
        for tool_name in EXPLAINER_PACK.capability_tools():
            self.tools.register(tool_name, lambda: None)

    def run(self, task: TaskEnvelope, **kwargs: object) -> GenericAgentExecution:
        brief = kwargs["brief"]
        generate = getattr(self.generator, "generate")
        planning = generate("", PlanningArtifact)
        script = generate("", ScriptArtifact)
        try:
            shots = compile_explainer_shots(planning, script)
        except ValueError:
            # Controller-focused tests can still exercise Critic rejection with
            # an intentionally incomplete script while satisfying the current
            # MCP execution contract that completed runs always include shots.
            shots = compile_explainer_shots(planning, sample_script(planning))
        actions = [
            ActionRecord(
                action_id=f"{task.task_id}:action:{index}",
                agent=self.name,
                task_id=task.task_id,
                tool=tool_name,
                result="success",
                duration_ms=0,
            )
            for index, tool_name in enumerate(
                EXPLAINER_PACK.capability_tools(),
                start=1,
            )
        ]
        return GenericAgentExecution(
            candidate=NarrativeCandidate(
                world_state=planning.world_state,
                planning=planning,
                script=script,
                shots=shots,
            ),
            result=AgentResult(
                task_id=task.task_id,
                status="completed",
                state_version_used=task.based_on_state_version,
                input_hash=task.input_hash,
                actions=actions,
                artifact_refs=[
                    ArtifactRef(kind="narrative_plan", id=brief.project_id),
                    ArtifactRef(kind="script", id=brief.project_id),
                    ArtifactRef(kind="shot_plan", id=brief.project_id),
                ],
                state_patch=StatePatch(
                    set={
                        "video.narrative_status": "ready",
                        "video.script_status": "ready",
                    },
                    invalidate=[
                        "voice:all",
                        "editorial:all",
                        "timeline:all",
                        "render:final",
                    ],
                ),
                evaluation_target=(
                    f"narrative:{brief.project_id}@"
                    f"{task.based_on_state_version + 1}"
                ),
            ),
        )


def narrative_controller_from_generator(
    generator: object,
    model_name: str,
    *,
    state_version: int = 0,
) -> NarrativeHarnessController:
    return NarrativeHarnessController(
        _GeneratedNarrativeAgent(generator),  # type: ignore[arg-type]
        NarrativeCritic(),
        model_name,
        state_version=state_version,
    )


def test_controller_builds_complete_narrative_artifact() -> None:
    brief = make_brief(topic="为什么测试很重要", duration_seconds=90)
    artifact = narrative_controller_from_generator(
        FakeGenerator(), "fake-model"
    ).run(brief).artifact

    assert artifact.schema_version == "narrative.v3"
    assert artifact.model == "fake-model"
    assert artifact.quality.passed is True
    assert len(artifact.planning.beats) == len(artifact.script.segments)


def test_controller_does_not_normalize_model_duration_hints() -> None:
    plan = sample_plan()
    plan.beats[-1].target_duration_ms = 3_250

    class DurationHintGenerator(FakeGenerator):
        def generate(self, prompt: str, output_type: type[T]) -> T:
            if output_type is PlanningArtifact:
                return plan  # type: ignore[return-value]
            return sample_script(plan)  # type: ignore[return-value]

    artifact = narrative_controller_from_generator(
        DurationHintGenerator(), "fake-model"
    ).run(make_brief(topic="测试软时长约束", duration_seconds=90)).artifact

    assert artifact.quality.planned_duration_ms == 82_000
    assert artifact.quality.passed is True
