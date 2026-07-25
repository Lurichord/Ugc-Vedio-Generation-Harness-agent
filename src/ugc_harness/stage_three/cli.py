from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..shared.artifacts import ArtifactWriter
from ..shared.llm import StructuredLLM
from ..shared.settings import LLMSettings
from ..stage_one.models import StageOneArtifact
from ..stage_two.models import VoiceStageArtifact
from .pipeline import EditorialStagePipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ugc-evidence-plan",
        description=(
            "读取内容与真实配音产物，生成主张、证据检索需求和视觉需求。"
        ),
    )
    parser.add_argument(
        "project",
        type=Path,
        help="包含 stage_one_artifact.json 和 stage_two_artifact.json 的项目目录",
    )
    parser.add_argument("--model", default=None)
    parser.add_argument("--api-keys-file", type=Path)
    parser.add_argument("--fail-on-quality-error", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    project_dir = args.project
    try:
        stage_one = StageOneArtifact.model_validate_json(
            (project_dir / "stage_one_artifact.json").read_text(encoding="utf-8")
        )
        stage_two = VoiceStageArtifact.model_validate_json(
            (project_dir / "stage_two_artifact.json").read_text(encoding="utf-8")
        )
        settings = LLMSettings.from_environment(args.api_keys_file, args.model)
        artifact = EditorialStagePipeline(
            StructuredLLM(settings),
            settings.model,
        ).run(stage_one, stage_two)
        written = ArtifactWriter(
            project_dir.parent
        ).write_editorial_stage(project_dir, artifact)
    except Exception as exc:
        print(f"证据与视觉规划阶段失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    summary = {
        "project_directory": str(project_dir.resolve()),
        "model": artifact.model,
        "claims": artifact.quality.claim_count,
        "factual_claims": artifact.quality.factual_claim_count,
        "interpretations": artifact.quality.interpretation_count,
        "visual_requirements": len(
            artifact.editorial_plan.visual_requirements
        ),
        "beat_visual_coverage": artifact.quality.beat_visual_coverage,
        "factual_evidence_request_coverage": (
            artifact.quality.factual_evidence_request_coverage
        ),
        "planned_evidence_beat_coverage": (
            artifact.quality.planned_evidence_beat_coverage
        ),
        "quality_passed": artifact.quality.passed,
        "quality_issues": artifact.quality.issues,
        "artifact_files": [path.name for path in written],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.fail_on_quality_error and not artifact.quality.passed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
