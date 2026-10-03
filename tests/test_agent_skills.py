from pathlib import Path

from ugc_harness.agents.skills import (
    SKILL_ACTIVATE,
    activate_agent_skill,
    list_agent_skills,
    load_agent_skill,
    skill_activate_spec,
    skill_dir_for,
)
from ugc_harness.harness.shot_asset_controller import generate_shot_video


def test_narrative_skills_are_markdown_playbooks() -> None:
    names = {item["name"] for item in list_agent_skills("narrative_agent")}
    assert names == {
        "write-actions",
        "write-beats",
        "write-definition",
        "write-explanations",
        "write-procedure",
        "write-script",
        "write-sections",
        "write-shots",
        "write-story",
        "write-world",
    }
    assert (skill_dir_for("narrative_agent") / "write-shots.md").is_file()
    body = load_agent_skill("write-shots", agent="narrative_agent")
    assert "narrative.write_shots" in body
    assert "narrative.write_identities" in body
    assert "一镜一件事" in body
    assert "generation_prompt" in body
    assert "一种景别" in body
    assert "ProductionShot" in body
    assert "shot_kind" in body
    assert "external_narration" in body
    assert "generated_scene" in body
    assert "procedure_demo" in body
    assert "前置" in load_agent_skill("write-beats", agent="narrative_agent")
    sections = load_agent_skill("write-sections", agent="narrative_agent")
    assert "video_profile" in sections
    assert "aroll_character" in sections
    assert "to_verify" in sections
    assert "一种景别" in load_agent_skill("write-actions", agent="narrative_agent")
    assert "payoff" in load_agent_skill("write-beats", agent="narrative_agent")
    assert "说明动画" in load_agent_skill("write-beats", agent="narrative_agent")
    assert "说明动画" in load_agent_skill("write-shots", agent="narrative_agent")
    assert "emphasis_words" in load_agent_skill("write-script", agent="narrative_agent")
    assert "同一套 id" in load_agent_skill("write-definition", agent="narrative_agent")
    assert "静态特征" in load_agent_skill("write-world", agent="narrative_agent")
    assert "静态特征" in load_agent_skill("write-definition", agent="narrative_agent")
    activated = activate_agent_skill("narrative_agent", "write-shots")
    assert activated["ok"] is True
    assert "write_identities" in activated["skill"]
    from ugc_harness.agents.generic import _history_tool_result

    stored: dict[str, str] = {}
    history = _history_tool_result(
        SKILL_ACTIVATE,
        {"ok": True, "name": "write-shots", "skill": body},
        stored,
    )
    assert history == {"ok": True, "name": "write-shots"}
    assert "skill" not in history
    assert stored["write-shots"] == body


def test_generate_shot_skill_is_for_asset_stage() -> None:
    names = {item["name"] for item in list_agent_skills("asset_stage")}
    assert names == {"generate-identity", "generate-shot"}
    assert (skill_dir_for("asset_stage") / "generate-identity.md").is_file()
    assert skill_dir_for("asset_stage") == skill_dir_for("asset_agent")
    assert list_agent_skills("voice_agent") == []
    body = load_agent_skill("generate-shot", agent="asset_stage")
    assert "reference_image" in body
    assert "last_frame:" in body
    assert "图片" in body
    assert "定义为" in body
    assert "{怎么又散了？}" in body
    assert "<冰箱低鸣>" in body
    assert "向后延长" in body
    assert "bridge_from_previous" in body
    assert "不要沿用上一镜的站位和机位" in body
    assert "不要生成背景音乐或BGM" in body
    assert "图片N是上一镜尾帧" in body
    assert "画外旁白说" not in body
    assert "<现场操作声>" in body
    assert "asset.list_graph" in body
    assert "prompt" in body
    activated = activate_agent_skill("asset_stage", "generate-shot")
    assert activated["ok"] is True
    assert "参考图" in activated["skill"]
    missing = activate_agent_skill("voice_agent", "generate-shot")
    assert missing["ok"] is False
    spec = skill_activate_spec("asset_stage")
    assert spec["name"] == SKILL_ACTIVATE
    assert "generate-shot" in spec["description"]
    assert "generate-identity" in spec["description"]
    identity = load_agent_skill("generate-identity", agent="asset_stage")
    assert "generation_prompt" in identity
    assert "asset.generate_identity" in identity
    assert "锁脸" in identity
    assert "不要为同一 `ref` 连出两张图" in identity
    assert "三视图" in identity
    activated_identity = activate_agent_skill("asset_stage", "generate-identity")
    assert activated_identity["ok"] is True


def test_generate_talking_head_skill_is_for_asset_agent() -> None:
    names = {item["name"] for item in list_agent_skills("asset_agent")}
    assert names == {"generate-talking-head"}
    assert "generate-talking-head" not in {
        item["name"] for item in list_agent_skills("asset_stage")
    }
    body = load_agent_skill("generate-talking-head", agent="asset_agent")
    assert "reference_image" in body
    assert "first_frame" in body
    assert "asset.generate_talking_head" in body
    assert "asset.list_talking_heads" in body
    assert "@音频1" in body
    assert "continue_from_previous" in body
    activated = activate_agent_skill("asset_agent", "generate-talking-head")
    assert activated["ok"] is True
    assert "口播" in activated["skill"]
    missing = activate_agent_skill("asset_stage", "generate-talking-head")
    assert missing["ok"] is False


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
