from __future__ import annotations

from pathlib import Path
from typing import Any

from .project_service import ProjectService
from .repository import AppDataRepository


class TaskProjector:
    def __init__(self, projects: ProjectService, repository: AppDataRepository) -> None:
        self.projects = projects
        self.repository = repository

    def view(self, project_dir: Path) -> dict[str, Any]:
        state = self.projects.read_state(project_dir)
        core_tasks: list[dict[str, Any]] = []
        for phase, trajectory in state.trajectory.phases.items():
            for record in trajectory.tasks:
                task = record.task
                core_tasks.append({
                    "source": "core",
                    "phase": phase,
                    "task_id": task.task_id,
                    "task_kind": record.task_kind,
                    "status": record.agent_result.status,
                    "recorded_at": record.recorded_at,
                    "beat_ids": task.scope.beat_ids,
                    "target_refs": task.scope.target_refs,
                    "goal": task.goal,
                    "critic_passed": record.evaluation.passed,
                    "issues": [item.model_dump(mode="json") for item in record.evaluation.issues],
                    "actions": [item.model_dump(mode="json") for item in record.agent_result.actions],
                    "graph_update": record.graph_update.model_dump(mode="json"),
                })
        app_events = [
            {"source": "app", **item.model_dump(mode="json")}
            for item in self.repository.events(project_dir.name)
        ]
        chronological = sorted(
            [*core_tasks, *app_events],
            key=lambda item: item.get("recorded_at") or item.get("created_at") or "",
        )
        by_phase: dict[str, list[dict[str, Any]]] = {}
        for item in chronological:
            phase = str(item.get("phase") or item.get("stage") or "unknown")
            by_phase.setdefault(phase, []).append(item)
        return {"by_phase": by_phase, "chronological": chronological}
