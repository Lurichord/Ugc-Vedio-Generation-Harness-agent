from __future__ import annotations

from pathlib import Path

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
from .models import VoiceArtifact, VoicePlan


class VoiceAgentExecution(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    artifact: VoiceArtifact | None = None
    result: AgentResult


class VoiceAgent(BaseAgent[VoiceAgentExecution]):
    name = "voice_agent"
    PLAN_TOOL = "voice.create_plan"
    SYNTHESIZE_TOOL = "audio.synthesize_narration"

    def __init__(self, tools, voice_id: str) -> None:
        super().__init__(tools)
        self.voice_id = voice_id

    def run(self, task: TaskEnvelope, **kwargs: object) -> VoiceAgentExecution:
        self.validate_task(task)
        narrative = kwargs.get("narrative")
        project_dir = kwargs.get("project_dir")
        if not isinstance(narrative, NarrativeArtifact):
            raise TypeError("VoiceAgent requires a NarrativeArtifact")
        if not isinstance(project_dir, (str, Path)):
            raise TypeError("VoiceAgent requires a project directory")
        actions: list[ActionRecord] = []
        try:
            plan = self.invoke_tool(
                task,
                actions,
                self.PLAN_TOOL,
                brief=narrative.brief,
                script=narrative.script,
                voice_id=self.voice_id,
                character=narrative.planning.world_state.aroll_character,
            )
            if not isinstance(plan, VoicePlan):
                raise TypeError("voice.create_plan returned an invalid VoicePlan")
            artifact = self.invoke_tool(
                task,
                actions,
                self.SYNTHESIZE_TOOL,
                narrative=narrative,
                voice_plan=plan,
                project_dir=project_dir,
            )
            if not isinstance(artifact, VoiceArtifact):
                raise TypeError("audio tool returned an invalid VoiceArtifact")
        except Exception as exc:
            return VoiceAgentExecution(result=failed_result(task, actions, exc))

        result = AgentResult(
            task_id=task.task_id,
            status="completed",
            state_version_used=task.based_on_state_version,
            input_hash=task.input_hash,
            actions=actions,
            artifact_refs=[ArtifactRef(kind="voice", id=narrative.brief.project_id)],
            state_patch=StatePatch(
                set={"video.voice_status": "ready"},
                invalidate=["editorial:all", "timeline:all", "render:final"],
            ),
            evaluation_target=(
                f"voice:{narrative.brief.project_id}@{task.based_on_state_version + 1}"
            ),
        )
        return VoiceAgentExecution(artifact=artifact, result=result)
