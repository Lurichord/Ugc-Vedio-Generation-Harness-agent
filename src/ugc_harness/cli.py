from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .artifacts import ArtifactWriter
from .llm import StructuredLLM
from .pipeline import StageOnePipeline, make_brief
from .settings import LLMSettings, TTSSettings
from .tts import VolcengineTTS
from .voice_pipeline import VoiceStagePipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ugc-harness",
        description="生成 UGC 第一阶段产物：内容结构、Planned Beats 与口播剧本。",
    )
    parser.add_argument("topic", help="视频主题")
    parser.add_argument(
        "--project-name",
        help="项目文件夹名称；默认使用主题名称",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=90,
        choices=range(60, 121),
        metavar="SECONDS",
        help="目标时长，60–120 秒（默认 90）",
    )
    parser.add_argument("--platform", default="douyin")
    parser.add_argument(
        "--audience",
        default="对主题感兴趣、但没有专业背景的普通用户",
    )
    parser.add_argument("--goal")
    parser.add_argument(
        "--tone",
        action="append",
        help="可重复传入，如 --tone conversational --tone humorous",
    )
    parser.add_argument(
        "--creator-persona",
        default="像朋友一样解释复杂话题的知识型创作者",
    )
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--api-keys-file",
        type=Path,
        help="包含 export OPENROUTER_API_KEY/OPENROUTER_BASE_URL 的配置文件",
    )
    parser.add_argument(
        "--output-root",
        "--output",
        dest="output_root",
        type=Path,
        default=Path("outputs"),
        help="所有项目输出的根目录（默认 outputs）",
    )
    parser.add_argument(
        "--fail-on-quality-error",
        action="store_true",
        help="结构质检出现 error 时返回非零状态",
    )
    parser.add_argument(
        "--with-voice",
        action="store_true",
        help="第一阶段通过后继续生成真实配音、词级时间戳和 Realized Beats",
    )
    parser.add_argument(
        "--voice-id",
        help="配音音色；默认读取 VOLCENGINE_TTS_VOICE_ID",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        settings = LLMSettings.from_environment(args.api_keys_file, args.model)
        brief = make_brief(
            topic=args.topic,
            project_name=args.project_name,
            duration_seconds=args.duration,
            platform=args.platform,
            audience=args.audience,
            goal=args.goal,
            tone=args.tone,
            creator_persona=args.creator_persona,
        )
        artifact = StageOnePipeline(
            StructuredLLM(settings), settings.model
        ).run(brief)
        writer = ArtifactWriter(args.output_root)
        project_dir, written_files = writer.write(artifact)
        voice_artifact = None
        if args.with_voice:
            tts_settings = TTSSettings.from_environment(args.api_keys_file)
            if args.voice_id:
                tts_settings.voice_id = args.voice_id
            with VolcengineTTS(tts_settings) as provider:
                voice_artifact = VoiceStagePipeline(
                    provider,
                    tts_settings.voice_id,
                ).run(artifact, project_dir)
            written_files.extend(
                writer.write_voice_stage(project_dir, voice_artifact)
            )
    except Exception as exc:
        print(f"生成失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    summary = {
        "output_directory": str(project_dir.resolve()),
        "artifact_files": [path.name for path in written_files],
        "model": artifact.model,
        "sections": len(artifact.planning.sections),
        "beats": len(artifact.planning.beats),
        "segments": len(artifact.script.segments),
        "estimated_duration_seconds": round(
            artifact.quality.estimated_script_duration_ms / 1000, 1
        ),
        "quality_passed": artifact.quality.passed,
        "quality_issues": [
            issue.model_dump(mode="json") for issue in artifact.quality.issues
        ],
    }
    if voice_artifact is not None:
        summary["voice"] = {
            "audio_file": voice_artifact.timed_audio.audio_file,
            "duration_seconds": round(
                voice_artifact.timed_audio.duration_ms / 1000, 2
            ),
            "word_alignment_coverage": voice_artifact.word_alignment.coverage,
            "realized_beats": len(voice_artifact.realized_beats),
            "quality_passed": voice_artifact.quality.passed,
        }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    voice_failed = voice_artifact is not None and not voice_artifact.quality.passed
    if args.fail_on_quality_error and (
        not artifact.quality.passed or voice_failed
    ):
        raise SystemExit(2)
