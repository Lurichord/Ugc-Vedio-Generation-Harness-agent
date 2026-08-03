from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ...harness.models import AgentResult, ArtifactRef, StatePatch, TaskEnvelope
from ..base import BaseAgent, failed_result
from ..timeline_agent.models import TimelineArtifact
from ..voice_agent.models import VoiceArtifact
from .models import RenderCandidate


class RenderAgentExecution(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    candidate: RenderCandidate | None = None
    result: AgentResult


class RenderAgent(BaseAgent[RenderAgentExecution]):
    name = "render_agent"
    RENDER_TOOL = "render.execute"

    def run(self, task: TaskEnvelope, **kwargs: object) -> RenderAgentExecution:
        self.validate_task(task)
        voice = kwargs.get("voice")
        timeline = kwargs.get("timeline")
        project_dir = kwargs.get("project_dir")
        if not isinstance(voice, VoiceArtifact):
            raise TypeError("RenderAgent requires a VoiceArtifact")
        if not isinstance(timeline, TimelineArtifact):
            raise TypeError("RenderAgent requires a TimelineArtifact")
        if not isinstance(project_dir, (str, Path)):
            raise TypeError("RenderAgent requires a project directory")
        actions = []
        try:
            candidate = self.invoke_tool(
                task,
                actions,
                self.RENDER_TOOL,
                voice=voice,
                timeline_artifact=timeline,
                project_dir=project_dir,
            )
            if not isinstance(candidate, RenderCandidate):
                raise TypeError("render.execute returned an invalid artifact")
        except Exception as exc:
            return RenderAgentExecution(result=failed_result(task, actions, exc))
        return RenderAgentExecution(
            candidate=candidate,
            result=AgentResult(
                task_id=task.task_id,
                status="completed",
                state_version_used=task.based_on_state_version,
                input_hash=task.input_hash,
                actions=actions,
                artifact_refs=[ArtifactRef(kind="render_candidate", id=voice.project_id)],
                state_patch=StatePatch(set={"video.render_status": "ready"}),
                evaluation_target=(
                    f"render:{voice.project_id}@{task.based_on_state_version + 1}"
                ),
            ),
        )
