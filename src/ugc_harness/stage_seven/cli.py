from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..shared.artifacts import ArtifactWriter
from ..shared.settings import LLMSettings
from ..stage_five.models import TimelineStageArtifact
from ..stage_four.models import AssetStageArtifact
from ..stage_two.models import VoiceStageArtifact
from .pipeline import ImagePreparationPipeline
from .providers import OpenRouterImageAnalyzer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ugc-images",
        description="分析并生成 1080×1920 的渲染就绪图片。",
    )
    parser.add_argument("project", type=Path)
    parser.add_argument("--model", default=None)
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
        stage_four = AssetStageArtifact.model_validate_json(
            (project_dir / "stage_four_artifact.json").read_text(encoding="utf-8")
        )
        stage_five = TimelineStageArtifact.model_validate_json(
            (project_dir / "stage_five_artifact.json").read_text(encoding="utf-8")
        )
        settings = LLMSettings.from_environment(
            args.api_keys_file,
            args.model,
        )
        with OpenRouterImageAnalyzer(settings) as analyzer:
            artifact = ImagePreparationPipeline(analyzer).run(
                stage_two,
                stage_four,
                stage_five,
                project_dir,
            )
        written = ArtifactWriter(
            project_dir.parent
        ).write_image_preparation_stage(project_dir, artifact)
    except Exception as exc:
        print(f"图片处理阶段失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    summary = {
        "project_directory": str(project_dir.resolve()),
        "eligible_images": artifact.quality.eligible_image_count,
        "processed_images": artifact.quality.processed_image_count,
        "blocked_images": artifact.quality.blocked_image_count,
        "low_resolution_inputs": (
            artifact.quality.low_resolution_input_count
        ),
        "analyzer_model": settings.model,
        "quality_passed": artifact.quality.passed,
        "quality_issues": artifact.quality.issues,
        "artifact_files": [path.name for path in written],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.fail_on_quality_error and not artifact.quality.passed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
