from pathlib import Path

from PIL import Image

from tests.test_editorial import _editorial_run, _plan
from tests.test_voice import _narrative, _voice_run
from ugc_harness.shared.artifacts import ArtifactWriter
from ugc_harness.shared.settings import LLMSettings
from ugc_harness.agents.asset_agent.models import AssetCard
from ugc_harness.agents.asset_agent.models import AssetUsabilityReview
from ugc_harness.agents.asset_agent.providers import OpenRouterWebAssetProvider
from ugc_harness.agents.asset_agent.providers import ProviderResult
from ugc_harness.agents.asset_agent.providers import _parse_asset_review
from ugc_harness.agents.asset_agent.providers import _validate_public_url
from ugc_harness.harness.asset_controller import AssetHarnessController
from ugc_harness.harness.dependencies import DependencyGraph
from ugc_harness.harness.dependency_builders import asset_commits, editorial_commits
from ugc_harness.harness.repair import RepairScheduler
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
    return AssetHarnessController.from_provider(provider).run(
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

    run = AssetHarnessController.from_provider(provider).run(
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
    assert run.record.project_state.video.timeline_status == "ready"
    assert "artifact:assets" in run.record.project_state.dependency_graph.nodes
    assert run.record.project_state.trajectory.phases["asset"].tasks
    graph_state = run.record.project_state.dependency_graph
    DependencyGraph(graph_state).validate_integrity()
    asset_node = graph_state.nodes["asset:asset_vr01"]
    assert "visual_requirement:vr01" in asset_node.depends_on
    assert "asset:asset_vr01" in graph_state.nodes[
        "visual_requirement:vr01"
    ].dependents
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
    assert run.record.project_state.video.asset_status == "needs_revision"
    assert run.record.project_state.video.timeline_status == "blocked"
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
    for ref in {
        "asset:asset_vr01",
        "visual_resolution:vr01",
        "artifact:assets",
    }:
        state.dependency_graph.nodes[ref].status = "stale"
    repair_plan = RepairScheduler().plan(state, ["artifact:assets"])
    task = repair_plan.tasks[0]
    provider = FakeAssetProvider()

    repaired = AssetHarnessController.from_provider(provider).run(
        editorial_run.artifact,
        voice,
        tmp_path,
        state,
        task,
        current_artifact=initial.artifact,
    )

    assert task.agent == "asset_agent"
    assert task.scope.visual_request_ids == ["vr01"]
    assert provider.calls == ["vr01_d01", "vr01_d02"]
    assert repaired.record.transition.to_agent == "repair_scheduler"
    assert repaired.record.project_state.trajectory.phases["asset"].tasks[-1].task_kind == "repair"
    assert all(
        repaired.record.project_state.dependency_graph.nodes[ref].status
        == "current"
        for ref in task.scope.target_refs
    )


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
                }
            ],
        }
        for index, beat in enumerate(voice.realized_beats[:2], start=1)
    ]
    # A1 -> A2 must continue the action. B-roll then breaks action continuity;
    # A3 must reuse identity but begin a new motion group.
    visuals[1]["visual_request_id"] = "vr02"
    visuals[1]["directions"][0]["direction_id"] = "vr02_d01"
    visuals.append(
        {
            **visuals[1],
            "visual_request_id": "vr04",
            "beat_id": voice.realized_beats[3].beat_id,
            "directions": [
                {**visuals[1]["directions"][0], "direction_id": "vr04_d01"}
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
    state.video.editorial_status = "passed"
    state.video.asset_status = "ready"
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
    artifact = AssetHarnessController.from_provider(provider).run(
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
    graph = asset_commits(artifact)
    graph_by_ref = {item.ref: item for item in graph}
    assert "character_reference:host_main" in graph_by_ref
    assert "asset:asset_vr01" in graph_by_ref["asset:asset_vr02"].depends_on
    assert "asset:asset_vr02" not in graph_by_ref["asset:asset_vr04"].depends_on


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
    class LoginBlockedProvider(OpenRouterWebAssetProvider):
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
