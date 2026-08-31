"""Run one harness stage task. Shared by intake repair and Studio."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import TaskEnvelope
from .repair import AGENT_STAGE


def run_repair_task(
    project_dir: str | Path,
    task: TaskEnvelope,
    instruction: str,
    *,
    runner: Any | None = None,
    options: Any | None = None,
) -> None:
    """Execute one RepairScheduler task through the stage runner."""

    directory = Path(project_dir)
    stage = AGENT_STAGE.get(task.agent)
    if stage is None:
        raise ValueError(f"no stage runner for {task.agent!r}")
    worker = runner or default_stage_runner(directory.parent)
    request = options if options is not None else _empty_stage_options()
    worker.run(
        stage,
        directory,
        request,
        task=task,
        feedback=instruction,
    )


def default_stage_runner(output_root: str | Path) -> Any:
    from app.backend.stage_runner import StageRunner

    return StageRunner(Path(output_root))


def _empty_stage_options() -> Any:
    from app.backend.schemas import RunStageRequest

    return RunStageRequest()
