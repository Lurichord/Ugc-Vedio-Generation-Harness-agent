from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ...harness.models import ProjectState
from ...harness.voice_controller import VoiceHarnessController
from ...shared.artifacts import ArtifactWriter
from ...shared.settings import TTSSettings
from ..narrative_agent.models import NarrativeArtifact
from .tts import VolcengineTTS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ugc-voice",
        description="运行 Voice Agent，生成配音、字级时间戳与 RealizedBeat。",
    )
    parser.add_argument("project", type=Path, help="Narrative 项目目录")
    parser.add_argument("--voice-id", help="覆盖默认火山 TTS 音色")
    parser.add_argument("--api-keys-file", type=Path)
    parser.add_argument("--fail-on-quality-error", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    project_dir = args.project
    try:
        narrative = NarrativeArtifact.model_validate_json(
            (project_dir / "narrative_artifact.json").read_text(encoding="utf-8")
        )
        state = ProjectState.model_validate_json(
            (project_dir / "harness" / "project_state.json").read_text(
                encoding="utf-8"
            )
        )
        settings = TTSSettings.from_environment(args.api_keys_file)
        if args.voice_id:
            settings.voice_id = args.voice_id
        else:
            character = narrative.planning.world_state.aroll_character
            if character is not None:
                settings.voice_id = settings.voice_for_gender(
                    character.voice_profile.gender
                )
        with VolcengineTTS(settings) as provider:
            run = VoiceHarnessController.from_provider(
                provider,
                settings.voice_id,
            ).run(narrative, project_dir, state)
        writer = ArtifactWriter(project_dir.parent)
        written = writer.write_voice(project_dir, run.artifact)
        written.extend(writer.write_voice_run(project_dir, run.record))
    except Exception as exc:
        print(f"Voice Agent 失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    summary = {
        "project_directory": str(project_dir.resolve()),
        "voice_id": settings.voice_id,
        "audio_file": run.artifact.timed_audio.audio_file,
        "audio_duration_seconds": round(
            run.artifact.timed_audio.duration_ms / 1000, 2
        ),
        "realized_beats": len(run.artifact.realized_beats),
        "quality_passed": run.record.evaluation.passed,
        "transition": run.record.transition.model_dump(mode="json"),
        "artifact_files": [path.name for path in written],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.fail_on_quality_error and not run.record.evaluation.passed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
