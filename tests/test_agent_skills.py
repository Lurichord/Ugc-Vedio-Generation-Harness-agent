from pathlib import Path

from ugc_harness.agents.skills import (
    SKILL_ACTIVATE,
    activate_agent_skill,
    list_agent_skills,
    load_agent_skill,
    skill_activate_spec,
)
from ugc_harness.harness.shot_asset_controller import generate_shot_video


def test_generate_shot_skill_is_for_asset_stage() -> None:
    names = {item["name"] for item in list_agent_skills("asset_stage")}
    assert names == {"generate-shot"}
    assert list_agent_skills("voice_agent") == []
    body = load_agent_skill("generate-shot", agent="asset_stage")
    assert "reference_image" in body
    assert "last_frame:" in body
    assert "@image" in body
    activated = activate_agent_skill("asset_stage", "generate-shot")
    assert activated["ok"] is True
    assert "参考图" in activated["skill"]
    missing = activate_agent_skill("voice_agent", "generate-shot")
    assert missing["ok"] is False
    spec = skill_activate_spec("asset_stage")
    assert spec["name"] == SKILL_ACTIVATE
    assert "generate-shot" in spec["description"]


def test_explicit_references_keep_agent_order(tmp_path: Path) -> None:
    from tests.test_production_routes import (
        DRAMA_HAPPY_PATH,
        FakeShotVideoProvider,
        _narrative_run,
        _plant_identity_images,
    )
    from ugc_harness.harness.shot_asset_controller import write_shot_last_frame
    from ugc_harness.harness.world_identities import bound_identity_refs

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
    write_shot_last_frame(tmp_path, previous_id, b"PNGFRAME")
    identity_id = bound_identity_refs(state)[0]
    provider = FakeShotVideoProvider()
    generate_shot_video(
        state,
        tmp_path,
        follow.spec.shot_id,
        provider,
        references=[
            {
                "id": identity_id,
                "description": "人物参考图，外貌必须一致。",
            },
            {
                "id": f"last_frame:{previous_id}",
                "description": "上一镜尾帧，动作从这里连续。",
            },
        ],
    )
    descriptions = [item["description"] for item in provider.calls[0]["reference_images"]]
    assert descriptions == [
        "人物参考图，外貌必须一致。",
        "上一镜尾帧，动作从这里连续。",
    ]
