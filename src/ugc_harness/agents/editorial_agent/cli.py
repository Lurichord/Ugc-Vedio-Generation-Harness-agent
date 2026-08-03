from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ...harness.editorial_controller import EditorialHarnessController
from ...harness.models import ProjectState
from ...shared.artifacts import ArtifactWriter
from ...shared.llm import StructuredLLM
from ...shared.settings import LLMSettings
from ..narrative_agent.models import NarrativeArtifact
from ..voice_agent.models import VoiceArtifact
from .models import EditorialArtifact


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ugc-visual-plan",
        description="运行 Editorial Agent，生成 Claim 与 A-roll/B-roll 视觉需求。",
    )
    parser.add_argument("project", type=Path, help="已完成 Voice Agent 的项目目录")
    parser.add_argument("--model", help="覆盖默认 LLM 模型")
    parser.add_argument("--api-keys-file", type=Path)
    parser.add_argument("--fail-on-quality-error", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    project_dir = args.project
    try:
        narrative = NarrativeArtifact.model_validate_json(
            (project_dir / "narrative_artifact.json").read_text(encoding="utf-8")
        )
        voice = VoiceArtifact.model_validate_json(
            (project_dir / "voice_artifact.json").read_text(encoding="utf-8")
        )
        state = ProjectState.model_validate_json(
            (project_dir / "harness" / "project_state.json").read_text(
                encoding="utf-8"
            )
        )
        current_artifact = None
        critic_problems: list[str] = []
        artifact_path = project_dir / "editorial_artifact.json"
        evaluation_path = project_dir / "harness" / "editorial_evaluation.json"
        if state.video.editorial_status == "needs_revision" and artifact_path.is_file():
            current_artifact = EditorialArtifact.model_validate_json(
                artifact_path.read_text(encoding="utf-8")
            )
            if evaluation_path.is_file():
                evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
                critic_problems = [
                    str(item.get("diagnosis") or "")
                    for item in evaluation.get("issues", [])
                    if item.get("diagnosis")
                ]
        settings = LLMSettings.from_environment(args.api_keys_file, args.model)
        run = EditorialHarnessController.from_generator(
            StructuredLLM(settings),
            settings.model,
        ).run(
            narrative,
            voice,
            state,
            current_artifact=current_artifact,
            critic_problems=critic_problems,
        )
        writer = ArtifactWriter(project_dir.parent)
        written = writer.write_editorial(project_dir, run.artifact)
        written.extend(writer.write_editorial_run(project_dir, run.record))
    except Exception as exc:
        print(f"Editorial Agent 失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    summary = {
        "project_directory": str(project_dir.resolve()),
        "model": settings.model,
        "claims": len(run.artifact.editorial_plan.claims),
        "visual_requirements": len(
            run.artifact.editorial_plan.visual_requirements
        ),
        "quality_passed": run.record.evaluation.passed,
        "transition": run.record.transition.model_dump(mode="json"),
        "artifact_files": [path.name for path in written],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.fail_on_quality_error and not run.record.evaluation.passed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
