from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ...harness.models import ProjectState
from ...harness.render_controller import RenderHarnessController
from ...shared.artifacts import ArtifactWriter
from ..timeline_agent.models import TimelineArtifact
from ..voice_agent.models import VoiceArtifact
from .capabilities import RenderCapabilities


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ugc-render", description="Render and independently review the final UGC video.")
    parser.add_argument("project", type=Path)
    parser.add_argument("--browser-executable", type=Path)
    parser.add_argument("--fail-on-quality-error", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    project_dir = args.project
    try:
        voice = VoiceArtifact.model_validate_json((project_dir / "voice_artifact.json").read_text(encoding="utf-8"))
        timeline = TimelineArtifact.model_validate_json((project_dir / "timeline_artifact.json").read_text(encoding="utf-8"))
        state = ProjectState.model_validate_json((project_dir / "harness" / "project_state.json").read_text(encoding="utf-8"))
        renderer = RenderCapabilities(browser_executable=args.browser_executable)
        run = RenderHarnessController.from_renderer(renderer.run).run(voice, timeline, project_dir, state)
        artifact = run.artifact
        writer = ArtifactWriter(project_dir.parent)
        written = writer.write_render(project_dir, artifact)
        written.extend(writer.write_render_run(project_dir, run.record))
    except Exception as exc:
        print(f"Render Agent failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    final = next(item for item in artifact.outputs if item.kind == "final")
    print(json.dumps({
        "project_directory": str(project_dir.resolve()),
        "final_video": final.local_path,
        "duration_ms": final.duration_ms,
        "resolution": f"{final.width}x{final.height}",
        "fps": final.fps,
        "quality_passed": artifact.quality.passed,
        "transition": run.record.transition.model_dump(mode="json"),
        "quality_issues": artifact.quality.issues,
        "artifact_files": [path.name for path in written],
    }, ensure_ascii=False, indent=2))
    if args.fail_on_quality_error and not artifact.quality.passed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
