from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ugc_harness.harness.models import TaskEnvelope
from ugc_harness.harness.repair import (
    drive_repair,
    invalidate_subgraph,
    write_project_state,
)
from ugc_harness.harness.stage_execution import run_repair_task
from ugc_harness.intake.actions import repair_lane_seeds
from ugc_harness.intake.graph_view import resolve_graph_ref

from .project_service import ProjectService
from .repository import AppDataRepository
from .schemas import RunStageRequest
from .stage_runner import StageRunner


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
            target = resolve_graph_ref(
                state.dependency_graph.nodes, feedback.target_ref
            )
            if target is None:
                raise ValueError("unknown repair target")
            seeds, _shot_refs, _identity_refs, _direct = repair_lane_seeds(
                state, [target]
            )
            invalidated = invalidate_subgraph(state, seeds)
            write_project_state(project_dir, state)
            options = RunStageRequest()

            def run_task(task: TaskEnvelope, instruction: str) -> None:
                run_repair_task(
                    project_dir,
                    task,
                    instruction,
                    runner=self.runner,
                    options=options,
                )

            result = drive_repair(
                desired_refs=invalidated,
                instruction=feedback.instruction,
                load_state=lambda: self.projects.read_state(project_dir),
                run_task=run_task,
            )
            if not result.ok:
                raise RuntimeError(result.error or "repair failed")
            feedback.status = "resolved"
            feedback.resolved_at = datetime.now(timezone.utc).isoformat()
            self.repository.update_feedback(storage_key, feedback)
        except Exception:
            feedback.status = "open"
            self.repository.update_feedback(storage_key, feedback)
            raise
