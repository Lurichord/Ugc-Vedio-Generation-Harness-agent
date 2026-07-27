from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..shared.artifacts import ArtifactWriter
from ..shared.settings import AssetGenerationSettings, LLMSettings
from ..stage_four.models import AssetStageArtifact
from ..stage_three.models import EditorialStageArtifact
from ..stage_two.models import VoiceStageArtifact
from .pipeline import TimelineCompositionPipeline
from .providers import OpenRouterScreenAnimationProvider


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ugc-timeline",
        description="以真实音频为主时钟，生成 UGC 剪辑时间线和字幕计划。",
    )
    parser.add_argument("project", type=Path)
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
        stage_four = AssetStageArtifact.model_validate_json(
            (project_dir / "stage_four_artifact.json").read_text(encoding="utf-8")
        )
        llm_settings = LLMSettings.from_environment(args.api_keys_file)
        generation_settings = AssetGenerationSettings.from_environment(
            args.api_keys_file,
            video_model=args.video_model,
        )
        with OpenRouterScreenAnimationProvider(
            llm_settings,
            generation_settings,
        ) as provider:
            artifact = TimelineCompositionPipeline(provider).run(
                stage_two,
                stage_three,
                stage_four,
                project_dir,
            )
        written = ArtifactWriter(
            project_dir.parent
        ).write_timeline_stage(project_dir, artifact)
    except Exception as exc:
        print(f"时间线编排阶段失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    summary = {
        "project_directory": str(project_dir.resolve()),
        "duration_ms": artifact.timeline.duration_ms,
        "clips": artifact.quality.clip_count,
        "caption_cues": artifact.quality.caption_cue_count,
        "screen_derivatives": artifact.quality.screen_derivative_count,
        "video_model": generation_settings.video_model,
        "quality_passed": artifact.quality.passed,
        "quality_issues": artifact.quality.issues,
        "artifact_files": [path.name for path in written],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.fail_on_quality_error and not artifact.quality.passed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
