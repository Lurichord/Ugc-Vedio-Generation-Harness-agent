from typing import TypeVar

from pydantic import BaseModel

from ugc_harness.models import PlanningArtifact, ScriptArtifact
from ugc_harness.pipeline import StageOnePipeline, make_brief
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


def test_pipeline_builds_complete_artifact() -> None:
    brief = make_brief(topic="为什么测试很重要", duration_seconds=90)
    artifact = StageOnePipeline(FakeGenerator(), "fake-model").run(brief)

    assert artifact.schema_version == "stage-one.v1"
    assert artifact.model == "fake-model"
    assert artifact.quality.passed is True
    assert len(artifact.planning.beats) == len(artifact.script.segments)


def test_pipeline_does_not_normalize_model_duration_hints() -> None:
    plan = sample_plan()
    plan.beats[-1].target_duration_ms = 3_250

    class DurationHintGenerator(FakeGenerator):
        def generate(self, prompt: str, output_type: type[T]) -> T:
            if output_type is PlanningArtifact:
                return plan  # type: ignore[return-value]
            return sample_script(plan)  # type: ignore[return-value]

    artifact = StageOnePipeline(DurationHintGenerator(), "fake-model").run(
        make_brief(topic="测试软时长约束", duration_seconds=90)
    )

    assert artifact.quality.planned_duration_ms == 82_000
    assert artifact.quality.passed is True
