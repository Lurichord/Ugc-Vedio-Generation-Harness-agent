from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from ...harness.models import (
    ActionRecord,
    AgentResult,
    ArtifactRef,
    StatePatch,
    TaskEnvelope,
)
from ..base import BaseAgent, failed_result
from ..narrative_agent.models import NarrativeArtifact
from ..voice_agent.models import VoiceArtifact
from .models import EditorialArtifact, EditorialPlan
from .prompts import editorial_plan_prompt, editorial_repair_prompt


class EditorialAgentExecution(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    plan: EditorialPlan | None = None
    result: AgentResult


class EditorialAgent(BaseAgent[EditorialAgentExecution]):
    name = "editorial_agent"
    PLAN_TOOL = "editorial.create_plan"

    def run(self, task: TaskEnvelope, **kwargs: object) -> EditorialAgentExecution:
        self.validate_task(task)
        narrative = kwargs.get("narrative")
        voice = kwargs.get("voice")
        current_artifact = kwargs.get("current_artifact")
        critic_problems = kwargs.get("critic_problems")
        if not isinstance(narrative, NarrativeArtifact):
            raise TypeError("EditorialAgent requires a NarrativeArtifact")
        if not isinstance(voice, VoiceArtifact):
            raise TypeError("EditorialAgent requires a VoiceArtifact")
        if current_artifact is not None and not isinstance(
            current_artifact, EditorialArtifact
        ):
            raise TypeError("current_artifact must be an EditorialArtifact")
        if critic_problems is not None and not isinstance(critic_problems, list):
            raise TypeError("critic_problems must be a list")
        prompt = editorial_plan_prompt(narrative, voice)
        if current_artifact is not None and critic_problems:
            prompt = editorial_repair_prompt(
                narrative,
                voice,
                current_artifact.editorial_plan.model_dump_json(indent=2),
                [str(item) for item in critic_problems],
            )
        actions: list[ActionRecord] = []
        try:
            plan = self.invoke_tool(
                task,
                actions,
                self.PLAN_TOOL,
                prompt=prompt,
                output_type=EditorialPlan,
            )
            if not isinstance(plan, EditorialPlan):
                raise TypeError("editorial.create_plan returned an invalid plan")
        except Exception as exc:
            return EditorialAgentExecution(result=failed_result(task, actions, exc))

        return EditorialAgentExecution(
            plan=plan,
            result=AgentResult(
                task_id=task.task_id,
                status="completed",
                state_version_used=task.based_on_state_version,
                input_hash=task.input_hash,
                actions=actions,
                artifact_refs=[
                    ArtifactRef(kind="editorial_plan", id=narrative.brief.project_id)
                ],
                state_patch=StatePatch(
                    set={"video.editorial_status": "ready"},
                    invalidate=["assets:all", "timeline:all", "render:final"],
                ),
                evaluation_target=(
                    f"editorial:{narrative.brief.project_id}@"
                    f"{task.based_on_state_version + 1}"
                ),
            ),
        )
