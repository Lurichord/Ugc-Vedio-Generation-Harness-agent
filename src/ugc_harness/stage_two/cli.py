from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..shared.artifacts import ArtifactWriter
from ..shared.settings import TTSSettings
from ..stage_one.models import StageOneArtifact
from .tts import VolcengineTTS
from .pipeline import VoiceStagePipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ugc-voice",
        description=(
            "读取第一阶段项目产物，生成 VoicePlan、真实配音、"
            "词级时间戳与 RealizedBeat。"
        ),
    )
    parser.add_argument(
        "project",
        type=Path,
        help="项目目录，或 stage_one_artifact.json 路径",
    )
    parser.add_argument(
        "--voice-id",
        help="覆盖 VOLCENGINE_TTS_VOICE_ID",
    )
    parser.add_argument(
        "--api-keys-file",
        type=Path,
        help="TTS API 配置文件；默认读取项目运行目录下的 .env",
    )
    parser.add_argument(
        "--fail-on-quality-error",
        action="store_true",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    source = (
        args.project / "stage_one_artifact.json"
        if args.project.is_dir()
        else args.project
    )
    project_dir = source.parent
    try:
        stage_one = StageOneArtifact.model_validate_json(
            source.read_text(encoding="utf-8")
        )
        settings = TTSSettings.from_environment(args.api_keys_file)
        if args.voice_id:
            settings.voice_id = args.voice_id
        with VolcengineTTS(settings) as provider:
            artifact = VoiceStagePipeline(
                provider,
                settings.voice_id,
            ).run(stage_one, project_dir)
        written = ArtifactWriter(project_dir.parent).write_voice_stage(
            project_dir,
            artifact,
        )
    except Exception as exc:
        print(f"语音阶段失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    summary = {
        "project_directory": str(project_dir.resolve()),
        "voice_id": settings.voice_id,
        "audio_file": artifact.timed_audio.audio_file,
        "audio_duration_seconds": round(
            artifact.timed_audio.duration_ms / 1000, 2
        ),
        "script_segments": len(artifact.voice_plan.segments),
        "aligned_words": artifact.word_alignment.word_count,
        "native_alignment_coverage": artifact.word_alignment.coverage,
        "realized_beats": len(artifact.realized_beats),
        "quality_passed": artifact.quality.passed,
        "quality_issues": artifact.quality.issues,
        "artifact_files": [path.name for path in written],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.fail_on_quality_error and not artifact.quality.passed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
