from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from app.backend.schemas import RunStageRequest
from app.backend.stage_runner import StageRunner
from ugc_harness.harness.models import ProjectState

SHOT_STAGES = ("asset", "timeline", "render")


def _state(project_dir: Path) -> ProjectState:
    return ProjectState.model_validate_json(
        (project_dir / "harness" / "project_state.json").read_text(encoding="utf-8")
    )


def remaining_stages(state: ProjectState) -> list[str]:
    pending: list[str] = []
    for stage in SHOT_STAGES:
        status = str(getattr(state.evaluation.stages, stage))
        if status != "passed":
            pending.append(stage)
    return pending


def main() -> None:
    parser = argparse.ArgumentParser(
        description="从身份参考图之后继续跑短剧/教程：出镜 → 时间线 → 成片。"
    )
    parser.add_argument("project", type=Path)
    args = parser.parse_args()
    project_dir = args.project.resolve()
    if not (project_dir / "harness" / "project_state.json").is_file():
        raise SystemExit(f"project state missing: {project_dir}")

    runner = StageRunner(project_dir.parent)
    options = RunStageRequest()
    stages = remaining_stages(_state(project_dir))
    print(
        json.dumps(
            {
                "project_directory": str(project_dir),
                "remaining": stages,
                "stages": _state(project_dir).evaluation.stages.model_dump(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    if not stages:
        print("nothing to run", flush=True)
        return
    for stage in stages:
        print(f"[continue] start {stage}", flush=True)
        runner.run(stage, project_dir, options)  # type: ignore[arg-type]
        print(
            json.dumps(
                {
                    "finished": stage,
                    "stages": _state(project_dir).evaluation.stages.model_dump(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
