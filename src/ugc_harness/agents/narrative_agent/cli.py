from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ...harness.controller import NarrativeHarnessController
from ...shared.artifacts import ArtifactWriter
from ...shared.llm import StructuredLLM
from ...shared.settings import LLMSettings
from .brief import make_brief


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ugc-harness",
        description="运行 Narrative Agent，生成内容世界、Planned Beats 与口播剧本。",
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
    parser.add_argument(
        "--production-mode",
        choices=["auto", "explainer", "drama", "tutorial"],
        default="auto",
        help="Narrative Format Pack；当前已安装 explainer、drama、tutorial",
    )
    parser.add_argument(
        "--video-profile",
        choices=["auto", "a_roll", "b_roll", "ab_roll"],
        default="auto",
        help="画面模式；默认由 AI 判断",
    )
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--api-keys-file",
        type=Path,
        help="包含 VOLCENGINE_ARK_API_KEY/VOLCENGINE_ARK_BASE_URL 的配置文件",
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
            production_mode=args.production_mode,
            video_profile=args.video_profile,
        )
        controller = NarrativeHarnessController.from_mcp(
            StructuredLLM(settings), settings.model
        )
        run = controller.run(brief)
        artifact = run.artifact
        writer = ArtifactWriter(args.output_root)
        project_dir, written_files = writer.write(artifact)
        written_files.extend(writer.write_narrative_run(project_dir, run.record))
    except Exception as exc:
        print(f"生成失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    summary = {
        "output_directory": str(project_dir.resolve()),
        "artifact_files": [path.name for path in written_files],
        "model": artifact.model,
        "production_mode": artifact.brief.production_mode,
        "planning_type": artifact.planning.planning_type,
        "planning_units": (
            len(getattr(artifact.planning, "beats", []))
            or len(getattr(artifact.planning, "actions", []))
        ),
        "script_segments": len(artifact.script.segments) if artifact.script else 0,
        "shots": len(artifact.shots.shots) if artifact.shots else 0,
        "video_profile": artifact.planning.video_profile.model_dump(mode="json"),
        "estimated_duration_seconds": round(
            artifact.quality.estimated_script_duration_ms / 1000, 1
        ),
        "quality_passed": artifact.quality.passed,
        "quality_issues": [
            issue.model_dump(mode="json") for issue in artifact.quality.issues
        ],
        "transition": run.record.transition.model_dump(mode="json"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.fail_on_quality_error and not artifact.quality.passed:
        raise SystemExit(2)
