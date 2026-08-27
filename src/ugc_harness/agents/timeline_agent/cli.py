from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ...harness.models import ProjectState
from ...harness.timeline_controller import TimelineHarnessController
from ...shared.artifacts import ArtifactWriter
from ...shared.settings import AssetGenerationSettings, LLMSettings
from ..asset_agent.models import AssetArtifact
from ..editorial_agent.models import EditorialArtifact
from ..voice_agent.models import VoiceArtifact
from .providers import VolcengineScreenAnimationProvider


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ugc-timeline", description="Compose and review an audio-clocked UGC timeline.")
    parser.add_argument("project", type=Path)
    parser.add_argument("--video-model", default=None)
    parser.add_argument("--api-keys-file", type=Path)
    parser.add_argument("--fail-on-quality-error", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    project_dir = args.project
    try:
        voice = VoiceArtifact.model_validate_json((project_dir / "voice_artifact.json").read_text(encoding="utf-8"))
        editorial = EditorialArtifact.model_validate_json((project_dir / "editorial_artifact.json").read_text(encoding="utf-8"))
        assets = AssetArtifact.model_validate_json((project_dir / "asset_artifact.json").read_text(encoding="utf-8"))
        state = ProjectState.model_validate_json((project_dir / "harness" / "project_state.json").read_text(encoding="utf-8"))
        llm_settings = LLMSettings.from_environment(args.api_keys_file)
        generation_settings = AssetGenerationSettings.from_environment(args.api_keys_file, video_model=args.video_model)
        with VolcengineScreenAnimationProvider(llm_settings, generation_settings) as provider:
            run = TimelineHarnessController.from_provider(provider).run(voice, editorial, assets, project_dir, state)
        artifact = run.artifact
        writer = ArtifactWriter(project_dir.parent)
        written = writer.write_timeline(project_dir, artifact)
        written.extend(writer.write_timeline_run(project_dir, run.record))
    except Exception as exc:
        print(f"Timeline Agent failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps({
        "project_directory": str(project_dir.resolve()),
        "duration_ms": artifact.timeline.duration_ms,
        "clips": artifact.quality.clip_count,
        "caption_cues": artifact.quality.caption_cue_count,
        "screen_derivatives": artifact.quality.screen_derivative_count,
        "video_model": generation_settings.video_model,
        "quality_passed": artifact.quality.passed,
        "transition": run.record.transition.model_dump(mode="json"),
        "quality_issues": artifact.quality.issues,
        "artifact_files": [path.name for path in written],
    }, ensure_ascii=False, indent=2))
    if args.fail_on_quality_error and not artifact.quality.passed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
