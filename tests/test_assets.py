from pathlib import Path

from PIL import Image

from tests.fixtures.tool_models import ExplainerAssetToolModel, QueueToolModel
from tests.test_editorial import _editorial_run, _plan
from tests.test_voice import _narrative, _voice_run
from ugc_harness.shared.artifacts import ArtifactWriter
from ugc_harness.shared.settings import LLMSettings
from ugc_harness.agents.asset_agent.models import AssetCard
from ugc_harness.agents.asset_agent.models import AssetUsabilityReview
from ugc_harness.agents.asset_agent.providers import VolcengineWebAssetProvider
from ugc_harness.agents.asset_agent.providers import ProviderResult
from ugc_harness.agents.asset_agent.providers import _parse_asset_review
from ugc_harness.agents.asset_agent.providers import _validate_public_url
from ugc_harness.harness.asset_controller import AssetHarnessController
from ugc_harness.harness.dependencies import DependencyGraph
from ugc_harness.harness.dependency_builders import asset_commits, editorial_commits
from ugc_harness.agents.editorial_agent.models import (
    EditorialPlan,
    EditorialQuality,
    EditorialArtifact,
    ExplorationDirection,
)


class FakeAssetProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def acquire(
        self,
        *,
        project_id: str,
        visual_request_id: str,
        beat,
        direction: ExplorationDirection,
        project_dir: Path,
        **kwargs,
    ) -> ProviderResult:
        self.calls.append(direction.direction_id)
        if direction.order == 1:
            return ProviderResult(None, "not_found", "第一个方向没有素材")
        output = project_dir / "assets" / "test" / f"{visual_request_id}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (800, 1200), (30, 80, 140)).save(output)
        return ProviderResult(
            AssetCard(
                asset_id=f"asset_{visual_request_id}",
                visual_request_id=visual_request_id,
                direction_id=direction.direction_id,
                beat_id=beat.beat_id,
                modality=direction.asset_type,
                origin="generated",
                local_path=output.relative_to(project_dir).as_posix(),
                mime_type="image/png",
                sha256="0" * 64,
                generated_media_disclosure_required=True,
                generator_model="fake-image-model",
                generation_prompt="test prompt",
            ),
            "success",
            "第二个方向成功",
        )


def _asset_run(editorial_run, voice_run, provider, project_dir: Path):
    return AssetHarnessController.from_provider(
        provider, tool_model=QueueToolModel()
    ).run(
        editorial_run.artifact,
        voice_run.artifact,
        project_dir,
        editorial_run.record.project_state,
    )


def test_asset_agent_stops_after_first_success(tmp_path: Path) -> None:
    narrative = _narrative()
    voice_run = _voice_run(narrative, tmp_path)
    voice = voice_run.artifact
    factual_beat_id = next(
        beat.beat_id
        for beat in voice.realized_beats
        if beat.planned_beat_id == "pb04"
    )
    plan = _plan(
        [beat.beat_id for beat in voice.realized_beats],
        narrative.brief.project_id,
        factual_beat_id,
    )
    first_visual = plan.visual_requirements[0]
    first_visual.directions.append(
        ExplorationDirection(
            direction_id="vr01_d03",
            order=3,
            description="不应该被调用的方向",
            visual_role="illustration",
            asset_type="ai_image",
            query="不应调用",
            grounding_requirement="contextual",
            generated_media_disclosure_required=True,
            must_not_imply=["不是事实记录"],
        )
    )
    editorial_run = _editorial_run(narrative, voice_run, plan)
    editorial = editorial_run.artifact
    provider = FakeAssetProvider()

    run = AssetHarnessController.from_provider(
        provider, tool_model=QueueToolModel()
    ).run(
        editorial,
        voice,
        tmp_path,
        editorial_run.record.project_state,
    )
    artifact = run.artifact
    writer = ArtifactWriter(tmp_path.parent)
    written = writer.write_assets(
        tmp_path,
        artifact,
    )
    written.extend(writer.write_asset_run(tmp_path, run.record))

    assert artifact.quality.passed is True
    assert artifact.quality.resolution_coverage == 1
    assert "vr01_d03" not in provider.calls
    assert all(
        len(resolution.attempts) == 2
        for resolution in artifact.resolutions
    )
    assert (tmp_path / "15_asset_cards.json").is_file()
    assert any(path.name == "asset_artifact.json" for path in written)
    assert run.record.transition.to_agent == "timeline_agent"
    assert run.record.project_state.evaluation.stages.timeline == "ready"
    assert "artifact:assets" not in run.record.project_state.dependency_graph.nodes
    assert run.record.project_state.trajectory.phases["asset"].tasks
    graph_state = run.record.project_state.dependency_graph
    DependencyGraph(graph_state).validate_integrity()
    assert not any(ref.startswith("asset:") for ref in graph_state.nodes)
    assert not any(ref.startswith("visual_requirement:") for ref in graph_state.nodes)
    assert {"asset_task.json", "asset_evaluation.json"} <= {
        path.name for path in written
    }


def test_asset_url_guard_rejects_private_ip_but_allows_public_domain() -> None:
    _validate_public_url("https://example.com/report")

    import pytest

    with pytest.raises(ValueError):
        _validate_public_url("http://127.0.0.1/private")


def test_asset_review_failure_stays_with_asset_agent(tmp_path: Path) -> None:
    class MissingProvider:
        def acquire(self, **kwargs):
            return ProviderResult(None, "not_found", "测试素材不存在")

    narrative = _narrative()
    voice_run = _voice_run(narrative, tmp_path)
    voice = voice_run.artifact
    factual_beat_id = next(
        beat.beat_id
        for beat in voice.realized_beats
        if beat.planned_beat_id == "pb04"
    )
    plan = _plan(
        [beat.beat_id for beat in voice.realized_beats],
        narrative.brief.project_id,
        factual_beat_id,
    )
    editorial_run = _editorial_run(narrative, voice_run, plan)

    run = _asset_run(editorial_run, voice_run, MissingProvider(), tmp_path)

    assert run.record.evaluation.passed is False
    assert run.record.transition.to_agent == "asset_agent"
    assert run.record.transition.outcome == "revise"
    assert run.record.project_state.evaluation.stages.asset == "needs_revision"
    assert run.record.project_state.evaluation.stages.timeline == "blocked"
    history = run.record.project_state.trajectory.phases["asset"].tasks
    assert history[0].graph_update.committed is False


def test_asset_agent_repairs_only_one_visual_branch(tmp_path: Path) -> None:
    narrative = _narrative()
    voice_run = _voice_run(narrative, tmp_path)
    voice = voice_run.artifact
    factual_beat_id = next(
        beat.beat_id
        for beat in voice.realized_beats
        if beat.planned_beat_id == "pb04"
    )
    plan = _plan(
        [beat.beat_id for beat in voice.realized_beats],
        narrative.brief.project_id,
        factual_beat_id,
    )
    editorial_run = _editorial_run(narrative, voice_run, plan)
    initial = _asset_run(
        editorial_run, voice_run, FakeAssetProvider(), tmp_path
    )
    state = initial.record.project_state
    state.evaluation.stages.asset = "ready"
    controller = AssetHarnessController.from_provider(
        FakeAssetProvider(), tool_model=QueueToolModel()
    )
    task = controller.create_task(editorial_run.artifact, voice, state)
    task.scope.visual_request_ids = ["vr01"]
    task.scope.beat_ids = [editorial_run.artifact.editorial_plan.visual_requirements[0].beat_id]
    provider = FakeAssetProvider()

    repaired = AssetHarnessController.from_provider(
        provider, tool_model=QueueToolModel()
    ).run(
        editorial_run.artifact,
        voice,
        tmp_path,
        state,
        task,
        current_artifact=initial.artifact,
    )

    assert task.scope.visual_request_ids == ["vr01"]
    assert provider.calls == ["vr01_d01", "vr01_d02"]
    assert repaired.record.evaluation.passed is True


def test_adjacent_a_roll_generates_linked_dynamic_clips(tmp_path: Path) -> None:
    narrative = _narrative()
    voice_run = _voice_run(narrative, tmp_path)
    voice = voice_run.artifact
    visuals = [
        {
            "visual_request_id": f"vr{index:02d}",
            "beat_id": beat.beat_id,
            "purpose": "固定人物口播",
            "track": "a_roll",
            "speaker_visible": True,
            "character_id": "host_main",
            "directions": [
                {
                    "direction_id": f"vr{index:02d}_d01",
                    "order": 1,
                    "description": "同一位知识创作者口播",
                    "visual_role": "host_delivery",
                    "asset_type": "talking_head",
                    "query": "30岁短发创作者，深色上衣，家庭书房",
                    "grounding_requirement": "none",
                    "generated_media_disclosure_required": True,
                },
                {
                    "direction_id": f"vr{index:02d}_d02",
                    "order": 2,
                    "description": "口播失败后的 AI 说明画面",
                    "visual_role": "illustration",
                    "asset_type": "ai_video",
                    "query": "同一主题的说明画面",
                    "grounding_requirement": "contextual",
                    "generated_media_disclosure_required": True,
                    "must_not_imply": ["不是口播现场"],
                },
            ],
        }
        for index, beat in enumerate(voice.realized_beats[:2], start=1)
    ]
    # A1 -> A2 must continue the action. B-roll then breaks action continuity;
    # A3 must reuse identity but begin a new motion group.
    visuals[1]["visual_request_id"] = "vr02"
    visuals[1]["directions"][0]["direction_id"] = "vr02_d01"
    visuals[1]["directions"][1]["direction_id"] = "vr02_d02"
    visuals.append(
        {
            **visuals[1],
            "visual_request_id": "vr04",
            "beat_id": voice.realized_beats[3].beat_id,
            "directions": [
                {**visuals[1]["directions"][0], "direction_id": "vr04_d01"},
                {**visuals[1]["directions"][1], "direction_id": "vr04_d02"},
            ],
        }
    )
    visuals.insert(
        2,
        {
            "visual_request_id": "vr03",
            "beat_id": voice.realized_beats[2].beat_id,
            "purpose": "evidence cutaway",
            "track": "b_roll",
            "speaker_visible": False,
            "directions": [
                {
                    "direction_id": "vr03_d01",
                    "order": 1,
                    "description": "evidence insert",
                    "visual_role": "illustration",
                    "asset_type": "ai_video",
                    "grounding_requirement": "contextual",
                    "generated_media_disclosure_required": True,
                }
            ],
        },
    )
    profile = {
        "requested": "ab_roll",
        "resolved": "ab_roll",
        "selection_source": "user",
        "rationale": "人物口播为主",
        "speaker_presence_ratio_min": 0.5,
        "speaker_presence_ratio_max": 0.8,
        "character_consistency_required": True,
        "character_id": "host_main",
        "character_description": "30岁短发创作者，深色上衣，家庭书房",
    }
    editorial = EditorialArtifact(
        model="fake-model",
        project_id=narrative.brief.project_id,
        editorial_plan=EditorialPlan(
            project_id=narrative.brief.project_id,
            video_profile=profile,
            claims=[],
            visual_requirements=visuals,
        ),
        quality=EditorialQuality(
            passed=True,
            beat_visual_coverage=1,
            claim_count=0,
            factual_claim_count=0,
            interpretation_count=0,
            speaker_presence_ratio=1,
        ),
    )
    state = voice_run.record.project_state.model_copy(deep=True)
    state.evaluation.stages.editorial = "passed"
    state.evaluation.stages.asset = "ready"
    DependencyGraph(state.dependency_graph).commit_batch(
        task_id="test_editorial_commit",
        produced_by="editorial_agent",
        commits=editorial_commits(editorial),
    )

    class TalkingHeadProvider:
        def __init__(self):
            self.calls = 0

        def acquire(self, **kwargs):
            self.calls += 1
            output = tmp_path / "assets" / f"host_{self.calls}.mp4"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"fake-mp4-video")
            direction = kwargs["direction"]
            beat = kwargs["beat"]
            request_id = kwargs["visual_request_id"]
            return ProviderResult(
                AssetCard(
                    asset_id=f"asset_{request_id}",
                    visual_request_id=request_id,
                    direction_id=direction.direction_id,
                    beat_id=beat.beat_id,
                    modality=direction.asset_type,
                    origin="generated",
                    local_path=output.relative_to(tmp_path).as_posix(),
                    mime_type="video/mp4",
                    sha256="1" * 64,
                    generated_media_disclosure_required=True,
                    generator_model="fake-model",
                    generation_prompt="fixed host",
                    character_id=kwargs["character_id"],
                    continuity_group_id=kwargs["continuity_group_id"],
                    previous_asset_id=(
                        kwargs["previous_character_asset"].asset_id
                        if kwargs["previous_character_asset"]
                        else None
                    ),
                    identity_reference_path=(
                        "assets/host_identity.png"
                        if kwargs["character_id"]
                        else None
                    ),
                ),
                "success",
                "generated",
            )

    provider = TalkingHeadProvider()
    artifact = AssetHarnessController.from_provider(
        provider, tool_model=QueueToolModel()
    ).run(
        editorial,
        voice,
        tmp_path,
        state,
    ).artifact

    assert provider.calls == 4
    assert len(artifact.assets) == 4
    assert len({item.local_path for item in artifact.assets}) == 4
    first, second, b_roll, after_b_roll = artifact.assets
    assert first.mime_type == second.mime_type == "video/mp4"
    assert second.previous_asset_id == first.asset_id
    assert second.continuity_group_id == first.continuity_group_id
    assert second.identity_reference_path == first.identity_reference_path
    assert b_roll.character_id is None
    assert after_b_roll.previous_asset_id is None
    assert after_b_roll.continuity_group_id != second.continuity_group_id
    assert after_b_roll.identity_reference_path == first.identity_reference_path
    assert asset_commits(artifact) == []


def test_talking_head_uses_reference_image_and_tts_audio(
    tmp_path: Path, monkeypatch
) -> None:
    from ugc_harness.agents.asset_agent.providers import (
        VolcengineGeneratedAssetProvider,
    )
    from ugc_harness.agents.voice_agent.models import RealizedBeat
    from ugc_harness.shared.settings import AssetGenerationSettings
    from ugc_harness.shared.video_generation import GeneratedVideo
    from tests.test_voice import _wav_bytes

    captured: dict[str, object] = {}

    def fake_seedance(client, settings, payload, **kwargs):
        captured["payload"] = payload
        return GeneratedVideo(
            "job-talk",
            b"mp4-bytes",
            "video/mp4",
            {"id": "job-talk"},
            last_frame=b"png-bytes",
        )

    monkeypatch.setattr(
        "ugc_harness.agents.asset_agent.providers.generate_seedance_video",
        fake_seedance,
    )
    identity = tmp_path / "assets" / "character_reference" / "host_main.png"
    identity.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), (10, 20, 30)).save(identity)
    audio = tmp_path / "audio" / "segments" / "vs01.wav"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(_wav_bytes(2500))
    beat = RealizedBeat(
        beat_id="b1",
        planned_beat_id="pb01",
        start_ms=0,
        end_ms=3000,
        duration_ms=3000,
        script_segment_ids=["s1"],
        audio_segment_ids=["a1"],
        narration="不同地区的中秋习俗为什么不一样",
        proposition="不同地区的中秋习俗为什么不一样",
        discourse_role="question",
        relation_to_previous="opening",
    )
    direction = ExplorationDirection(
        direction_id="vr01_d01",
        order=1,
        description="同一位知识创作者口播",
        visual_role="host_delivery",
        asset_type="talking_head",
        query="30岁短发创作者",
        grounding_requirement="none",
        generated_media_disclosure_required=True,
    )
    provider = VolcengineGeneratedAssetProvider(
        LLMSettings(api_key="test-api-key", base_url="https://ark.test/api/v3"),
        AssetGenerationSettings(
            video_api_key="test-key-1234",
            video_base_url="https://ark.test/api/v3",
            video_poll_seconds=0,
        ),
    )
    prompt = (
        "画面无任何字幕。开口说{不同地区的中秋习俗为什么不一样}，"
        "口型与这句话同步，音色和节奏跟@音频1走。"
    )
    try:
        blocked = provider.acquire(
            project_id="p",
            visual_request_id="vr01",
            beat=beat,
            direction=direction,
            project_dir=tmp_path,
            character_id="host_main",
            character_description="30岁短发创作者，深色上衣，家庭书房",
        )
        result = provider.acquire(
            project_id="p",
            visual_request_id="vr01",
            beat=beat,
            direction=direction,
            project_dir=tmp_path,
            character_id="host_main",
            character_description="30岁短发创作者，深色上衣，家庭书房",
            generation_prompt=prompt,
            generation_references=[
                {"id": "identity:host_main", "description": "口播人设"}
            ],
            reference_audio_path=audio,
        )
    finally:
        provider.close()

    assert blocked.status == "not_supported"
    assert result.status == "success"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["generate_audio"] is True
    assert payload["prompt"] == prompt
    assert all(
        frame.get("role") == "reference_image"
        for frame in payload["frame_images"]
    )
    assert all(
        frame.get("frame_type") != "first_frame"
        for frame in payload["frame_images"]
    )
    assert payload["audio_urls"][0]["role"] == "reference_audio"


def test_explainer_asset_agent_writes_talking_head_prompt(tmp_path: Path) -> None:
    narrative = _narrative()
    voice_run = _voice_run(narrative, tmp_path)
    voice = voice_run.artifact
    beat = voice.realized_beats[0]
    visuals = [
        {
            "visual_request_id": "vr01",
            "beat_id": beat.beat_id,
            "purpose": "固定人物口播",
            "track": "a_roll",
            "speaker_visible": True,
            "character_id": "host_main",
            "directions": [
                {
                    "direction_id": "vr01_d01",
                    "order": 1,
                    "description": "同一位知识创作者口播",
                    "visual_role": "host_delivery",
                    "asset_type": "talking_head",
                    "query": "30岁短发创作者，深色上衣，家庭书房",
                    "grounding_requirement": "none",
                    "generated_media_disclosure_required": True,
                },
                {
                    "direction_id": "vr01_d02",
                    "order": 2,
                    "description": "口播失败后的 AI 说明画面",
                    "visual_role": "illustration",
                    "asset_type": "ai_image",
                    "query": "同一主题的说明画面",
                    "grounding_requirement": "contextual",
                    "generated_media_disclosure_required": True,
                },
            ],
        }
    ]
    editorial = EditorialArtifact(
        model="fake-model",
        project_id=narrative.brief.project_id,
        editorial_plan=EditorialPlan(
            project_id=narrative.brief.project_id,
            video_profile={
                "requested": "ab_roll",
                "resolved": "ab_roll",
                "selection_source": "user",
                "rationale": "人物口播为主",
                "speaker_presence_ratio_min": 0.5,
                "speaker_presence_ratio_max": 0.8,
                "character_consistency_required": True,
                "character_id": "host_main",
                "character_description": "30岁短发创作者，深色上衣，家庭书房",
            },
            claims=[],
            visual_requirements=visuals,
        ),
        quality=EditorialQuality(
            passed=True,
            beat_visual_coverage=1,
            claim_count=0,
            factual_claim_count=0,
            interpretation_count=0,
            speaker_presence_ratio=1,
        ),
    )
    state = voice_run.record.project_state.model_copy(deep=True)
    state.evaluation.stages.editorial = "passed"
    state.evaluation.stages.asset = "ready"
    DependencyGraph(state.dependency_graph).commit_batch(
        task_id="test_editorial_commit",
        produced_by="editorial_agent",
        commits=editorial_commits(editorial),
    )

    class RecordingTalkingHeadProvider:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def acquire(self, **kwargs):
            direction = kwargs["direction"]
            prompt = str(kwargs.get("generation_prompt") or "")
            if direction.asset_type == "talking_head" and not prompt.strip():
                return ProviderResult(
                    None,
                    "not_supported",
                    "口播镜请用 asset.generate_talking_head 自己写提示词",
                )
            if direction.asset_type == "talking_head":
                self.prompts.append(prompt)
            output = tmp_path / "assets" / f"{kwargs['visual_request_id']}.mp4"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"fake-mp4-video")
            beat = kwargs["beat"]
            return ProviderResult(
                AssetCard(
                    asset_id=f"asset_{kwargs['visual_request_id']}",
                    visual_request_id=kwargs["visual_request_id"],
                    direction_id=direction.direction_id,
                    beat_id=beat.beat_id,
                    modality=direction.asset_type,
                    origin="generated",
                    local_path=output.relative_to(tmp_path).as_posix(),
                    mime_type="video/mp4",
                    sha256="1" * 64,
                    generated_media_disclosure_required=True,
                    generator_model="fake-model",
                    generation_prompt=prompt or "fallback",
                    character_id=kwargs.get("character_id"),
                    continuity_group_id=kwargs.get("continuity_group_id"),
                    identity_reference_path=(
                        "assets/character_reference/host_main.png"
                        if kwargs.get("character_id")
                        else None
                    ),
                ),
                "success",
                "generated",
            )

    provider = RecordingTalkingHeadProvider()
    run = AssetHarnessController.from_provider(
        provider, tool_model=ExplainerAssetToolModel()
    ).run(
        editorial,
        voice,
        tmp_path,
        state,
    )
    assert provider.prompts
    assert "@音频1" in provider.prompts[0]
    assert run.artifact.assets[0].modality == "talking_head"
    assert "画面无任何字幕" in (run.artifact.assets[0].generation_prompt or "")


def test_asset_review_parser_records_login_obstruction() -> None:
    review = _parse_asset_review(
        """{"usable":false,"login_or_auth_overlay":true,
        "obstruction_level":"major","reason":"登录弹窗覆盖主体"}""",
        "review-model",
    )

    assert review.usable is False
    assert review.login_or_auth_overlay is True
    assert review.obstruction_level == "major"


def test_web_provider_deletes_login_blocked_capture(tmp_path: Path) -> None:
    class LoginBlockedProvider(VolcengineWebAssetProvider):
        def _search_one_source(self, direction, beat):
            return {
                "url": "https://example.com/report",
                "title": "Report",
                "publisher": "Example",
            }

        def _capture_page(self, url: str, output: Path) -> None:
            output.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(1024))

        def _review_web_asset(self, image_path: Path, mime_type: str):
            return AssetUsabilityReview(
                reviewer_model="fake-reviewer",
                usable=False,
                login_or_auth_overlay=True,
                obstruction_level="major",
                reason="登录弹窗覆盖主体",
            )

    narrative = _narrative()
    voice = _voice_run(narrative, tmp_path).artifact
    direction = ExplorationDirection(
        direction_id="vr_login_d01",
        order=1,
        description="网页报告截图",
        visual_role="context",
        asset_type="source_screenshot",
        query="report",
        grounding_requirement="contextual",
    )
    provider = LoginBlockedProvider(
        LLMSettings(
            api_key="test-api-key",
            base_url="https://openrouter.ai/api/v1",
        ),
        edge_path=tmp_path / "fake-edge.exe",
    )
    try:
        result = provider.acquire(
            project_id="test",
            visual_request_id="vr_login",
            beat=voice.realized_beats[0],
            direction=direction,
            project_dir=tmp_path,
        )
    finally:
        provider.close()

    assert result.asset is None
    assert result.status == "not_found"
    assert "登录" in result.reason
    assert not (
        tmp_path / "assets" / "source_screenshot" / "asset_vr_login.png"
    ).exists()


def test_explainer_generation_prompt_allows_sourced_charts_not_logos() -> None:
    from ugc_harness.agents.asset_agent.providers import _generation_prompt
    from ugc_harness.agents.voice_agent.models import RealizedBeat

    beat = RealizedBeat(
        beat_id="b1",
        planned_beat_id="pb01",
        start_ms=0,
        end_ms=4000,
        duration_ms=4000,
        script_segment_ids=["s1"],
        audio_segment_ids=["a1"],
        narration="北方小麦种植面积大于南方水稻。",
        proposition="北方小麦种植面积大于南方水稻。",
        discourse_role="explanation",
        relation_to_previous="opening",
    )
    direction = ExplorationDirection(
        direction_id="d1",
        order=1,
        description="有出处的对比柱状图静帧",
        visual_role="explanation",
        asset_type="ai_image",
        query="9:16简洁柱状图，仅标注口播数字",
        grounding_requirement="contextual",
        generated_media_disclosure_required=True,
        must_not_imply=["不是官方统计截图"],
    )
    prompt = _generation_prompt(beat, direction)
    assert "画面无任何字幕" in prompt
    assert "Spoken voice-over (do not render)" in prompt
    assert "No caption bar" in prompt
    assert "simple sourced chart" in prompt
    assert "Never fill the frame with a logo" in prompt
    assert "Do not invent statistics" in prompt
    assert "Narration context:" not in prompt
    assert "dense small text" not in prompt
