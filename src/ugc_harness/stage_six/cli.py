from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..shared.artifacts import ArtifactWriter
from ..stage_five.models import TimelineStageArtifact
from ..stage_seven.models import ImagePreparationStageArtifact
from ..stage_two.models import VoiceStageArtifact
from .pipeline import FinalRenderPipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ugc-render",
        description="根据时间线、字幕和处理素材渲染最终竖屏 UGC 视频。",
    )
    parser.add_argument("project", type=Path)
    parser.add_argument("--browser-executable", type=Path)
    parser.add_argument("--fail-on-quality-error", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    project_dir = args.project
    try:
        stage_two = VoiceStageArtifact.model_validate_json(
            (project_dir / "stage_two_artifact.json").read_text(encoding="utf-8")
        )
        stage_five = TimelineStageArtifact.model_validate_json(
            (project_dir / "stage_five_artifact.json").read_text(encoding="utf-8")
        )
        stage_seven = ImagePreparationStageArtifact.model_validate_json(
            (project_dir / "stage_seven_artifact.json").read_text(encoding="utf-8")
        )
        artifact = FinalRenderPipeline(
            browser_executable=args.browser_executable,
        ).run(
            stage_two,
            stage_five,
            stage_seven,
            project_dir,
        )
        written = ArtifactWriter(
            project_dir.parent
        ).write_render_stage(project_dir, artifact)
    except Exception as exc:
        print(f"最终渲染阶段失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    final = next(item for item in artifact.outputs if item.kind == "final")
    summary = {
        "project_directory": str(project_dir.resolve()),
        "final_video": final.local_path,
        "duration_ms": final.duration_ms,
        "resolution": f"{final.width}x{final.height}",
        "fps": final.fps,
        "video_codec": final.video_codec,
        "audio_codec": final.audio_codec,
        "quality_passed": artifact.quality.passed,
        "quality_issues": artifact.quality.issues,
        "artifact_files": [path.name for path in written],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.fail_on_quality_error and not artifact.quality.passed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
