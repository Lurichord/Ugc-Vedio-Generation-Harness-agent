from __future__ import annotations

import json
from pathlib import Path

from ugc_harness.agents.narrative_agent.models import NarrativeArtifact
from ugc_harness.harness.models import ProjectState
from ugc_harness.harness.shot_asset_controller import ShotAssetHarnessController
from ugc_harness.harness.shot_render_controller import ShotRenderHarnessController
from ugc_harness.harness.shot_timeline_controller import ShotTimelineHarnessController
from ugc_harness.harness.shot_video_provider import SeedanceShotVideoProvider
from ugc_harness.shared.artifacts import ArtifactWriter
from ugc_harness.shared.settings import AssetGenerationSettings


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "outputs" / "端午节完整视频新链路"
SOURCES = {
    "drama": ROOT / "outputs" / "端午节三类型narrative_live" / "端午节_剧情演绎类_live",
    "tutorial": ROOT / "outputs" / "端午节三类型narrative_live" / "端午节_制作教程类_live",
}


def prepare(format_id: str, source: Path) -> tuple[Path, NarrativeArtifact, ProjectState]:
    narrative = NarrativeArtifact.model_validate_json(
        (source / "narrative_artifact.json").read_text(encoding="utf-8")
    )
    state = ProjectState.model_validate_json(
        (source / "harness" / "project_state.json").read_text(encoding="utf-8")
    ).model_copy(deep=True)
    project_id = f"full_duanwu_{format_id}"
    narrative = narrative.model_copy(update={
        "brief": narrative.brief.model_copy(update={
            "project_id": project_id,
            "project_name": f"端午节_{format_id}_完整AI视频",
        })
    })
    state.video.project_id = project_id
    state.video.state_version = 1
    state.video.narrative_status = "passed"
    state.video.script_status = "not_required" if format_id == "drama" else "passed"
    state.video.voice_status = "not_required"
    state.video.editorial_status = "not_required"
    state.video.asset_status = "ready"
    state.video.timeline_status = "pending"
    state.video.render_status = "pending"
    state.dependency_graph.nodes = {}
    state.dependency_graph.graph_version = 0
    state.trajectory.phases = {}
    writer = ArtifactWriter(OUTPUT_ROOT)
    project_dir, _ = writer.write(narrative)
    harness_dir = project_dir / "harness"
    harness_dir.mkdir(parents=True, exist_ok=True)
    (harness_dir / "project_state.json").write_text(
        state.model_dump_json(indent=2), encoding="utf-8"
    )
    return project_dir, narrative, state


def main() -> None:
    generation = AssetGenerationSettings.from_environment()
    summary: list[dict[str, object]] = []
    for format_id, source in SOURCES.items():
        project_dir, narrative, state = prepare(format_id, source)
        assert narrative.shots is not None
        print(
            f"[full] {format_id}: {len(narrative.shots.shots)} shots",
            flush=True,
        )
        with SeedanceShotVideoProvider(generation) as provider:
            assets, asset_run = ShotAssetHarnessController(provider).run(
                narrative, project_dir, state
            )
        writer = ArtifactWriter(OUTPUT_ROOT)
        writer.write_shot_assets(project_dir, assets)
        writer.write_shot_asset_run(project_dir, asset_run)
        timeline, timeline_run = ShotTimelineHarnessController().run(
            narrative, assets, asset_run.project_state
        )
        writer.write_shot_timeline(project_dir, timeline)
        writer.write_shot_timeline_run(project_dir, timeline_run)
        render, render_run = ShotRenderHarnessController().run(
            timeline, project_dir, timeline_run.project_state
        )
        writer.write_render(project_dir, render)
        writer.write_shot_render_run(project_dir, render_run)
        summary.append({
            "format_id": format_id,
            "project_dir": str(project_dir),
            "shot_count": len(assets.assets),
            "duration_ms": render.outputs[0].duration_ms,
            "final": render.outputs[0].local_path,
            "has_audio": render.outputs[0].has_audio,
            "audio_codec": render.outputs[0].audio_codec,
            "quality_passed": render.quality.passed,
        })
        print(f"[full] {format_id}: complete", flush=True)
    result = OUTPUT_ROOT / "full_summary.json"
    result.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(result, flush=True)


if __name__ == "__main__":
    main()
