from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ugc_harness.harness.models import ProjectState
from ugc_harness.harness.repair import RepairScheduler

from .project_service import ProjectService
from .repository import AppDataRepository
from .schemas import FeedbackRecord, RunStageRequest, StageName
from .stage_runner import StageRunner


AGENT_STAGE: dict[str, StageName] = {
    "narrative_agent": "narrative",
    "voice_agent": "voice",
    "editorial_agent": "editorial",
    "asset_agent": "asset",
    "timeline_agent": "timeline",
    "render_agent": "render",
}
ARTIFACT_REF: dict[StageName, str] = {
    "narrative": "artifact:narrative",
    "voice": "artifact:voice",
    "editorial": "artifact:editorial",
    "asset": "artifact:assets",
    "timeline": "artifact:timeline",
    "render": "artifact:render",
}


class RepairService:
    def __init__(
        self,
        projects: ProjectService,
        repository: AppDataRepository,
        runner: StageRunner,
    ) -> None:
        self.projects = projects
        self.repository = repository
        self.runner = runner

    def run(self, project_dir: Path, feedback_id: str) -> None:
        state = self.projects.read_state(project_dir)
        storage_key = project_dir.name
        matches = [
            item for item in self.repository.feedback(storage_key)
            if item.feedback_id == feedback_id
        ]
        if not matches:
            raise ValueError("feedback not found")
        feedback = matches[0]
        feedback.status = "repairing"
        self.repository.update_feedback(storage_key, feedback)
        try:
            self._invalidate(state, feedback.target_ref)
            self._write_state(project_dir, state)
            desired = ARTIFACT_REF[feedback.stage]
            for _ in range(12):
                current = self.projects.read_state(project_dir)
                plan = RepairScheduler().plan(current, [desired])
                if plan.blockers:
                    raise RuntimeError("; ".join(item.reason for item in plan.blockers))
                if plan.complete:
                    feedback.status = "resolved"
                    feedback.resolved_at = datetime.now(timezone.utc).isoformat()
                    self.repository.update_feedback(storage_key, feedback)
                    return
                if not plan.tasks:
                    raise RuntimeError("repair scheduler produced no runnable task")
                for task in plan.tasks:
                    stage = AGENT_STAGE[task.agent]
                    self.runner.run(
                        stage,
                        project_dir,
                        RunStageRequest(),
                        task=task,
                        feedback=feedback.instruction,
                    )
            raise RuntimeError("repair did not converge within 12 scheduler rounds")
        except Exception:
            feedback.status = "open"
            self.repository.update_feedback(storage_key, feedback)
            raise

    @staticmethod
    def _invalidate(state: ProjectState, target_ref: str) -> None:
        if target_ref not in state.dependency_graph.nodes:
            raise ValueError("unknown repair target")
        queue = [target_ref]
        visited: set[str] = set()
        while queue:
            ref = queue.pop(0)
            if ref in visited:
                continue
            visited.add(ref)
            node = state.dependency_graph.nodes[ref]
            if node.locked:
                raise ValueError(f"dependency node is locked: {ref}")
            node.status = "stale"
            queue.extend(node.dependents)

    @staticmethod
    def _write_state(project_dir: Path, state: ProjectState) -> None:
        path = project_dir / "harness" / "project_state.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(path)
