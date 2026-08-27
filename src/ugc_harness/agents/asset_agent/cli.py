from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ...harness.asset_controller import AssetHarnessController
from ...harness.models import EvaluationResult, ProjectState
from ...shared.artifacts import ArtifactWriter
from ...shared.settings import AssetGenerationSettings, LLMSettings
from ..editorial_agent.models import EditorialArtifact
from ..voice_agent.models import VoiceArtifact
from .image_analysis import VolcengineImageAnalyzer
from .models import AssetArtifact
from .providers import RoutedAssetProvider


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ugc-assets",
        description="按 first-success 规则获取每个 Beat 的第一份合格素材。",
    )
    parser.add_argument("project", type=Path)
    parser.add_argument("--model", default=None)
    parser.add_argument("--image-model", default=None)
    parser.add_argument("--video-model", default=None)
    parser.add_argument("--api-keys-file", type=Path)
    parser.add_argument("--fail-on-quality-error", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    project_dir = args.project
    try:
        voice = VoiceArtifact.model_validate_json(
            (project_dir / "voice_artifact.json").read_text(encoding="utf-8")
        )
        editorial = EditorialArtifact.model_validate_json(
            (project_dir / "editorial_artifact.json").read_text(encoding="utf-8")
        )
        state = ProjectState.model_validate_json(
            (project_dir / "harness" / "project_state.json").read_text(
                encoding="utf-8"
            )
        )
        current_artifact = None
        prior_evaluation = None
        artifact_path = project_dir / "asset_artifact.json"
        evaluation_path = project_dir / "harness" / "asset_evaluation.json"
        if state.video.asset_status == "needs_revision" and artifact_path.is_file():
            current_artifact = AssetArtifact.model_validate_json(
                artifact_path.read_text(encoding="utf-8")
            )
            if evaluation_path.is_file():
                prior_evaluation = EvaluationResult.model_validate_json(
                    evaluation_path.read_text(encoding="utf-8")
                )
        settings = LLMSettings.from_environment(args.api_keys_file, args.model)
        generation_settings = AssetGenerationSettings.from_environment(
            args.api_keys_file,
            image_model=args.image_model,
            video_model=args.video_model,
        )
        with (
            RoutedAssetProvider(settings, generation_settings) as provider,
            VolcengineImageAnalyzer(settings) as image_analyzer,
        ):
            controller = AssetHarnessController.from_provider(
                provider, image_analyzer
            )
            task = (
                controller.create_revision_task(
                    editorial,
                    voice,
                    state,
                    current_artifact,
                    prior_evaluation,
                )
                if current_artifact is not None and prior_evaluation is not None
                else None
            )
            run = controller.run(
                editorial,
                voice,
                project_dir,
                state,
                task,
                current_artifact=current_artifact,
            )
        artifact = run.artifact
        writer = ArtifactWriter(project_dir.parent)
        written = writer.write_assets(project_dir, artifact)
        written.extend(writer.write_asset_run(project_dir, run.record))
    except Exception as exc:
        print(f"素材获取阶段失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    summary = {
        "project_directory": str(project_dir.resolve()),
        "resolved": artifact.quality.resolved_count,
        "unresolved": artifact.quality.unresolved_count,
        "resolution_coverage": artifact.quality.resolution_coverage,
        "first_success_violations": (
            artifact.quality.first_success_violations
        ),
        "quality_passed": artifact.quality.passed,
        "transition": run.record.transition.model_dump(mode="json"),
        "image_model": generation_settings.image_model,
        "video_model": generation_settings.video_model,
        "quality_issues": artifact.quality.issues,
        "artifact_files": [path.name for path in written],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.fail_on_quality_error and not artifact.quality.passed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
