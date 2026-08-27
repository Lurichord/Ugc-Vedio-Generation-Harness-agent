from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_narrative_mcp import (
    DRAMA_HAPPY_PATH,
    TUTORIAL_HAPPY_PATH,
    ScriptedToolModel,
    _fixture_server,
)
from ugc_harness.agents.narrative_agent import make_brief
from ugc_harness.harness.controller import NarrativeHarnessController
from ugc_harness.harness.production_routes import resolve_production_route
from ugc_harness.harness.shot_asset_controller import (
    GeneratedShotVideo,
    ShotAssetHarnessController,
)
from ugc_harness.harness.shot_timeline_controller import ShotTimelineHarnessController


class FakeShotVideoProvider:
    def __init__(self) -> None:
        self.shot_ids: list[str] = []

    def generate(self, shot, *, progress_path: Path) -> GeneratedShotVideo:
        self.shot_ids.append(shot.shot_id)
        return GeneratedShotVideo(
            content=f"video:{shot.shot_id}".encode(),
            model="fake-seedance",
            prompt=f"prompt:{shot.shot_id}",
            job_id=f"job:{shot.shot_id}",
            duration_ms=4000,
        )


@pytest.mark.parametrize(
    ("format_id", "tools", "audio_mode"),
    [
        ("drama", DRAMA_HAPPY_PATH, "embedded_in_video"),
        ("tutorial", TUTORIAL_HAPPY_PATH, "mixed"),
    ],
)
def test_drama_and_tutorial_assets_are_ai_video_only(
    tmp_path: Path,
    format_id: str,
    tools: list[str],
    audio_mode: str,
) -> None:
    narrative_run = NarrativeHarnessController.from_mcp(
        ScriptedToolModel(tools),
        "fixture-model",
        server=_fixture_server(),
    ).run(make_brief(topic="端午节", production_mode=format_id))
    narrative = narrative_run.artifact
    route = resolve_production_route(narrative)
    assert route.asset_route == "shot_ai_video"
    assert "voice" not in route.stages
    assert "editorial" not in route.stages

    provider = FakeShotVideoProvider()
    artifact, run = ShotAssetHarnessController(provider).run(
        narrative,
        tmp_path,
        narrative_run.record.project_state,
    )

    assert artifact.format_id == format_id
    assert provider.shot_ids == run.task.scope.shot_ids
    assert len(artifact.assets) == len(provider.shot_ids)
    assert all(asset.modality == "ai_video" for asset in artifact.assets)
    assert all(asset.audio_mode == audio_mode for asset in artifact.assets)
    assert all(asset.preserve_source_audio for asset in artifact.assets)
    assert run.task.forbidden_actions == [
        "search_web_assets",
        "generate_images",
        "read_editorial_artifact",
        "read_voice_artifact",
        "modify_narrative",
    ]
    assert run.project_state.video.asset_status == "passed"
    assert run.project_state.video.timeline_status == "ready"
    assert all(action.result == "success" for action in run.actions)

    timeline, timeline_run = ShotTimelineHarnessController().run(
        narrative, artifact, run.project_state
    )
    assert [clip.shot_id for clip in timeline.clips] == provider.shot_ids
    assert all(clip.modality == "ai_video" for clip in timeline.clips)
    assert all(clip.preserve_source_audio for clip in timeline.clips)
    assert all(
        current.end_ms == following.start_ms
        for current, following in zip(timeline.clips, timeline.clips[1:])
    )
    assert timeline_run.project_state.video.timeline_status == "passed"
    assert timeline_run.project_state.video.render_status == "ready"
