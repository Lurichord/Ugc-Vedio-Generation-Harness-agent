from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.tool_models import PendingItemToolModel
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
    ShotReferenceImage,
    generate_shot_video,
    load_shot_last_frame,
    write_shot_last_frame,
)
from ugc_harness.harness.shot_reference_controller import ShotReferenceHarnessController
from ugc_harness.harness.shot_timeline_controller import ShotTimelineHarnessController
from ugc_harness.harness.world_identities import (
    bound_identity_refs,
    identity_record,
    set_reference_image,
)


class FakeIdentityImageProvider:
    def __init__(self) -> None:
        self.refs: list[str] = []

    def generate(self, *, ref: str, prompt: str, output: Path) -> bytes:
        del prompt
        self.refs.append(ref)
        output.parent.mkdir(parents=True, exist_ok=True)
        data = f"img:{ref}".encode()
        output.write_bytes(data)
        return data


class FakeShotVideoProvider:
    def __init__(self) -> None:
        self.shot_ids: list[str] = []
        self.calls: list[dict[str, object]] = []

    def generate(
        self,
        shot,
        *,
        progress_path: Path,
        reference_images: tuple[ShotReferenceImage, ...] | list[ShotReferenceImage] = (),
    ) -> GeneratedShotVideo:
        del progress_path
        self.shot_ids.append(shot.shot_id)
        self.calls.append(
            {
                "shot_id": shot.shot_id,
                "reference_images": [
                    {"path": item.path, "description": item.description}
                    for item in reference_images
                ],
            }
        )
        return GeneratedShotVideo(
            content=f"video:{shot.shot_id}".encode(),
            model="fake-seedance",
            prompt=f"prompt:{shot.shot_id}",
            job_id=f"job:{shot.shot_id}",
            duration_ms=4000,
            last_frame=b"PNGFRAME",
        )


def _narrative_run(format_id: str, tools: list[str]):
    return NarrativeHarnessController.from_mcp(
        ScriptedToolModel(tools),
        "fixture-model",
        server=_fixture_server(),
    ).run(make_brief(topic="端午节", production_mode=format_id))


def _run_references(narrative, project_dir: Path, state):
    return ShotReferenceHarnessController.from_provider(
        FakeIdentityImageProvider(),
        tool_model=PendingItemToolModel("ref"),
    ).run(narrative, project_dir, state)


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
    narrative_run = _narrative_run(format_id, tools)
    narrative = narrative_run.artifact
    state = narrative_run.record.project_state
    route = resolve_production_route(narrative)
    assert route.asset_route == "shot_ai_video"
    assert route.stages == ("references", "asset", "timeline", "render")
    assert "voice" not in route.stages
    assert "editorial" not in route.stages
    assert state.evaluation.stages.references == "ready"
    assert state.evaluation.stages.asset == "pending"

    _, reference_run = _run_references(narrative, tmp_path, state)
    next_state = reference_run.project_state
    assert next_state.evaluation.stages.references == "passed"
    assert next_state.evaluation.stages.asset == "ready"
    for ref in bound_identity_refs(next_state):
        item = identity_record(next_state, ref)
        assert item is not None
        assert item.reference_image
        assert (tmp_path / item.reference_image).is_file()
        assert next_state.dependency_graph.nodes[ref].status == "current"
        assert next_state.dependency_graph.nodes[ref].produced_by == "references_agent"

    provider = FakeShotVideoProvider()
    artifact, run = ShotAssetHarnessController(
        provider,
        tool_model=PendingItemToolModel("shot_id"),
    ).run(narrative, tmp_path, next_state)

    assert artifact.format_id == format_id
    assert provider.shot_ids == run.task.scope.shot_ids
    assert len(artifact.assets) == len(provider.shot_ids)
    assert all(asset.modality == "ai_video" for asset in artifact.assets)
    assert all(asset.audio_mode == audio_mode for asset in artifact.assets)
    assert all(asset.preserve_source_audio for asset in artifact.assets)
    assert all(asset.beat_id == asset.shot_id for asset in artifact.assets)
    assert run.task.forbidden_actions == [
        "search_web_assets",
        "generate_images",
        "read_editorial_artifact",
        "read_voice_artifact",
        "modify_narrative",
    ]
    assert run.project_state.evaluation.stages.asset == "passed"
    assert run.project_state.evaluation.stages.timeline == "ready"
    assert all(action.result == "success" for action in run.actions)
    by_id = {shot.spec.shot_id: shot for shot in run.project_state.production.shots}
    for call in provider.calls:
        shot = by_id[str(call["shot_id"])]
        images = call["reference_images"]
        assert images
        if shot.continuity.join == "continue_from_previous":
            assert "尾帧" in str(images[0]["description"])
        else:
            assert all("尾帧" not in str(item["description"]) for item in images)
        media = by_id[shot.spec.shot_id].media
        assert media is not None
        assert media.local_path
        node = run.project_state.dependency_graph.nodes[f"shot:{shot.spec.shot_id}"]
        assert node.status == "current"

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
    assert timeline_run.project_state.evaluation.stages.timeline == "passed"
    assert timeline_run.project_state.evaluation.stages.render == "ready"


def test_submit_accepts_on_disk_shots_without_media(tmp_path: Path) -> None:
    narrative_run = _narrative_run("drama", DRAMA_HAPPY_PATH)
    narrative = narrative_run.artifact
    state = narrative_run.record.project_state
    _, reference_run = _run_references(narrative, tmp_path, state)
    next_state = reference_run.project_state
    folder = tmp_path / "assets" / "generated_shots"
    folder.mkdir(parents=True, exist_ok=True)
    for shot in next_state.production.shots:
        (folder / f"asset_{shot.spec.shot_id}.mp4").write_bytes(
            f"video:{shot.spec.shot_id}".encode()
        )
        assert shot.media is None
    provider = FakeShotVideoProvider()
    artifact, run = ShotAssetHarnessController(
        provider,
        tool_model=PendingItemToolModel("shot_id"),
    ).run(narrative, tmp_path, next_state)
    assert provider.shot_ids == []
    assert [asset.shot_id for asset in artifact.assets] == [
        shot.spec.shot_id for shot in next_state.production.shots
    ]
    assert run.project_state.evaluation.stages.asset == "passed"
    assert all(shot.media is not None for shot in run.project_state.production.shots)


def test_generate_shot_requires_identity_images(tmp_path: Path) -> None:
    narrative_run = _narrative_run("drama", DRAMA_HAPPY_PATH)
    shot_id = narrative_run.artifact.shots.shots[0].shot_id
    with pytest.raises(RuntimeError, match="身份参考图缺失"):
        generate_shot_video(
            narrative_run.record.project_state,
            tmp_path,
            shot_id,
            FakeShotVideoProvider(),
        )


def test_generate_shot_requires_previous_clip(tmp_path: Path) -> None:
    narrative_run = _narrative_run("drama", DRAMA_HAPPY_PATH)
    state = narrative_run.record.project_state
    for ref in bound_identity_refs(state):
        relative = f"assets/identity_reference/{ref.replace(':', '_')}.png"
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"img")
        set_reference_image(state, ref, relative)
    follow = next(
        shot
        for shot in state.production.shots
        if shot.continuity.join == "continue_from_previous"
    )
    with pytest.raises(RuntimeError, match="上一镜"):
        generate_shot_video(
            state,
            tmp_path,
            follow.spec.shot_id,
            FakeShotVideoProvider(),
        )


def test_load_shot_last_frame_uses_saved_png(tmp_path: Path) -> None:
    path = write_shot_last_frame(tmp_path, "shot_a1", b"PNGFRAME")
    loaded = load_shot_last_frame(tmp_path, "shot_a1")
    assert loaded == path
    assert loaded.read_bytes() == b"PNGFRAME"


def test_load_shot_last_frame_downloads_job_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    job_path = tmp_path / "harness" / "seedance_jobs" / "shot_a1.json"
    job_path.parent.mkdir(parents=True)
    job_path.write_text(
        json.dumps(
            {
                "job": {
                    "content": {"last_frame_url": "https://example.test/last.png"}
                }
            }
        ),
        encoding="utf-8",
    )

    class _Response:
        content = b"FROMURL"

        def raise_for_status(self) -> None:
            return None

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def get(self, url: str, timeout: float):
            assert url == "https://example.test/last.png"
            return _Response()

    monkeypatch.setattr("ugc_harness.shared.video_generation.httpx.Client", _Client)
    path = load_shot_last_frame(tmp_path, "shot_a1")
    assert path is not None
    assert path.read_bytes() == b"FROMURL"


def _plant_identity_images(state, project_dir: Path) -> None:
    for ref in bound_identity_refs(state):
        relative = f"assets/identity_reference/{ref.replace(':', '_')}.png"
        path = project_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"img")
        set_reference_image(state, ref, relative)


def test_generate_shot_requires_previous_last_frame(tmp_path: Path) -> None:
    narrative_run = _narrative_run("drama", DRAMA_HAPPY_PATH)
    state = narrative_run.record.project_state
    _plant_identity_images(state, tmp_path)
    follow = next(
        shot
        for shot in state.production.shots
        if shot.continuity.join == "continue_from_previous"
    )
    previous_id = follow.continuity.previous_shot_id
    video = tmp_path / "assets" / "generated_shots" / f"asset_{previous_id}.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"mp4")
    with pytest.raises(RuntimeError, match="Seedance 尾帧"):
        generate_shot_video(
            state,
            tmp_path,
            follow.spec.shot_id,
            FakeShotVideoProvider(),
        )


def test_continue_shot_uses_seedance_last_frame(tmp_path: Path) -> None:
    narrative_run = _narrative_run("drama", DRAMA_HAPPY_PATH)
    state = narrative_run.record.project_state
    _plant_identity_images(state, tmp_path)
    follow = next(
        shot
        for shot in state.production.shots
        if shot.continuity.join == "continue_from_previous"
    )
    previous_id = follow.continuity.previous_shot_id
    video = tmp_path / "assets" / "generated_shots" / f"asset_{previous_id}.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"mp4")
    frame = write_shot_last_frame(tmp_path, previous_id, b"PNGFRAME")
    provider = FakeShotVideoProvider()
    generate_shot_video(state, tmp_path, follow.spec.shot_id, provider)
    first = provider.calls[0]["reference_images"][0]
    assert first["path"] == frame
    assert "尾帧" in str(first["description"])


def test_continue_payload_uses_reference_image_mode(tmp_path: Path) -> None:
    from ugc_harness.harness.shot_video_provider import _frame_images, _reference_prompt

    last = tmp_path / "last.png"
    identity = tmp_path / "identity.png"
    last.write_bytes(b"first")
    identity.write_bytes(b"ref")
    refs = [
        ShotReferenceImage(path=last, description="上一镜的尾帧。本镜动作从这里连续。"),
        ShotReferenceImage(path=identity, description="人物参考图。"),
    ]
    frames = _frame_images(refs)
    assert [item["role"] for item in frames] == ["reference_image", "reference_image"]
    prompt = _reference_prompt(refs)
    assert "@image1 是上一镜的尾帧" in prompt
    assert "@image2 是人物参考图" in prompt
