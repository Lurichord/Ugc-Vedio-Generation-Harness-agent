from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..shared.artifacts import ArtifactWriter
from ..shared.settings import AssetGenerationSettings, LLMSettings
from ..stage_three.models import EditorialStageArtifact
from ..stage_two.models import VoiceStageArtifact
from .pipeline import AssetAcquisitionPipeline
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
        stage_two = VoiceStageArtifact.model_validate_json(
            (project_dir / "stage_two_artifact.json").read_text(encoding="utf-8")
        )
        stage_three = EditorialStageArtifact.model_validate_json(
            (project_dir / "stage_three_artifact.json").read_text(encoding="utf-8")
        )
        settings = LLMSettings.from_environment(args.api_keys_file, args.model)
        generation_settings = AssetGenerationSettings.from_environment(
            args.api_keys_file,
            image_model=args.image_model,
            video_model=args.video_model,
        )
        with RoutedAssetProvider(settings, generation_settings) as provider:
            artifact = AssetAcquisitionPipeline(provider).run(
                stage_three,
                stage_two.realized_beats,
                project_dir,
            )
        written = ArtifactWriter(
            project_dir.parent
        ).write_asset_stage(project_dir, artifact)
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
