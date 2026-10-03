from pathlib import Path

from ugc_harness.content import (
    DramaShotPayload,
    DramaVisualSpec,
    EmbeddedSceneAudioSpec,
    MixedAudioSpec,
    ProductionShot,
    TimingSpec,
    TutorialShotPayload,
    TutorialVisualSpec,
)
from ugc_harness.harness.shot_asset_controller import (
    GRAPH_TOOL,
    ShotAssetHarnessController,
    ShotReferenceImage,
    _list_shots,
    generate_shot_video,
    resolve_shot_references,
    shot_materials,
)
from ugc_harness.harness.shot_prompt import compose_shot_prompt
from ugc_harness.harness.shot_video_provider import _shot_prompt
from ugc_harness.intake.graph_view import list_graph


def _ref(
    tmp_path: Path,
    filename: str,
    *,
    material_id: str,
    name: str = "",
    kind: str = "identity",
) -> ShotReferenceImage:
    path = tmp_path / filename
    path.write_bytes(b"img")
    return ShotReferenceImage(
        path=path,
        description="",
        material_id=material_id,
        kind=kind,
        name=name,
    )


def _drama_shot(
    *,
    action: str = "他端起碗，看向门口",
    camera: str = "中景，保持人物与动作清晰可见",
    dialogue: list[str] | None = None,
) -> ProductionShot:
    lines = ["还没好吗？"] if dialogue is None else dialogue
    return ProductionShot(
        shot_id="shot_a1",
        order=1,
        shot_kind="drama",
        purpose="催促",
        source_refs=["scene:s1", "action:a1"],
        binds=["cast:azhou", "place:kitchen", "prop:bowl"],
        visual=DramaVisualSpec(
            scene_id="scene_03",
            character_ids=["azhou"],
            location_id="kitchen",
            action_description=action,
            camera_instruction=camera,
            continuity_constraints=["cast:azhou 衣服不变"],
            generation_prompt=(
                "剧情场景 scene_03，地点 kitchen。"
                "身份绑定：cast:azhou；place:kitchen；prop:bowl。"
            ),
        ),
        audio=EmbeddedSceneAudioSpec(dialogue_lines=lines, ambient_audio="厨房环境声"),
        timing=TimingSpec(duration_driver="generated_clip", target_duration_ms=5000),
        payload=DramaShotPayload(
            scene_id="scene_03",
            action_ids=["a1"],
            dialogue_lines=lines,
        ),
    )


def _tutorial_shot(*, narration: str | None = None) -> ProductionShot:
    return ProductionShot(
        shot_id="shot_t1",
        order=1,
        shot_kind="tutorial",
        purpose="面团揉光滑",
        source_refs=["step:s1", "action:a1"],
        binds=["prop:dough"],
        visual=TutorialVisualSpec(
            step_id="s1",
            action_id="a1",
            subject_ref="dough",
            hands_required=True,
            camera_angle="close_up",
            critical_detail="双手把面团揉圆",
        ),
        audio=MixedAudioSpec(
            narration_segment_ids=["te01"] if narration else [],
            preserve_source_audio=True,
            source_audio_types=["tool_sound"],
        ),
        timing=TimingSpec(duration_driver="demonstration_action", target_duration_ms=8000),
        payload=TutorialShotPayload(
            step_id="s1",
            action_ids=["a1"],
            narration_text=narration,
        ),
    )


def test_drama_prompt_fills_slots_without_compiler_ids(tmp_path: Path) -> None:
    refs = [
        _ref(tmp_path, "azhou.png", material_id="cast:azhou", name="阿舟"),
        _ref(tmp_path, "kitchen.png", material_id="place:kitchen", name="厨房"),
        _ref(tmp_path, "bowl.png", material_id="prop:bowl", name="碗"),
    ]
    prompt = compose_shot_prompt(_drama_shot(), refs)
    assert prompt == _shot_prompt(_drama_shot(), refs)
    assert prompt.startswith("画面无任何字幕。")
    assert "将图片1定义为阿舟。" in prompt
    assert "阿舟的面部特征参考图片1。" in prompt
    assert "将图片2定义为厨房。" in prompt
    assert "将图片3定义为碗。" in prompt
    assert "参考图片1中的阿舟，生成接下来的画面。" in prompt
    assert "9:16 竖屏中景，真实室内光线，手机直出。" in prompt
    assert "阿舟站在厨房里。" in prompt
    assert "镜头1：机位固定。他端起碗，看向门口。开口说{还没好吗？}，口型与这句话同步。" in prompt
    assert "<厨房环境声>" in prompt
    assert "面部、发型、衣服始终与图片1一致" in prompt
    assert "房间布局与图片2一致" in prompt
    assert "碗的形状与图片3一致" in prompt
    assert "不要生成任何文字、字幕、Logo、水印" in prompt
    assert "scene_03" not in prompt
    assert "cast:azhou" not in prompt
    assert "剧情场景" not in prompt
    assert "@image" not in prompt


def test_prop_lock_does_not_invent_a_hold(tmp_path: Path) -> None:
    refs = [
        _ref(tmp_path, "azhou.png", material_id="cast:azhou", name="阿舟"),
        _ref(tmp_path, "kitchen.png", material_id="place:kitchen", name="厨房"),
        _ref(tmp_path, "bowl.png", material_id="prop:bowl", name="碗"),
    ]
    prompt = compose_shot_prompt(_drama_shot(action="他看向门口"), refs)
    assert "他看向门口" in prompt
    assert "端起" not in prompt
    assert "碗的形状与图片3一致" in prompt


def test_continue_slot_leads_the_subject(tmp_path: Path) -> None:
    refs = [
        _ref(
            tmp_path,
            "last.png",
            material_id="last_frame:shot_a1",
            name="上一镜尾帧",
            kind="previous_last_frame",
        ),
        _ref(tmp_path, "azhou.png", material_id="cast:azhou", name="阿舟"),
        _ref(tmp_path, "kitchen.png", material_id="place:kitchen", name="厨房"),
    ]
    prompt = compose_shot_prompt(_drama_shot(action="他看向门口", dialogue=[]), refs)
    assert "图片1是上一镜尾帧。本镜起幅与图片1同一姿态、同一机位，不要跳变。" in prompt
    assert "将图片2定义为阿舟。" in prompt
    assert "阿舟站在厨房里。" in prompt
    assert "图片1是上一镜尾帧；起幅与图片1同一姿态、同一机位" in prompt


def test_bridge_slot_does_not_lock_pose(tmp_path: Path) -> None:
    refs = [
        _ref(tmp_path, "azhou.png", material_id="cast:azhou", name="阿舟"),
        _ref(tmp_path, "kitchen.png", material_id="place:kitchen", name="厨房"),
        _ref(
            tmp_path,
            "last.png",
            material_id="last_frame:shot_a1",
            name="上一镜尾帧",
            kind="bridge_last_frame",
        ),
    ]
    prompt = compose_shot_prompt(_drama_shot(action="他看向门口", dialogue=[]), refs)
    assert "将图片1定义为阿舟。" in prompt
    assert "本镜起幅与图片1同一姿态、同一机位" not in prompt
    assert "本镜起幅与图片3同一姿态、同一机位" not in prompt
    assert "图片3是上一镜尾帧，只用来延续妆造、服装、材料外观、场景结构和光影，不要沿用上一镜的站位和机位。" in prompt


def test_camera_move_comes_from_instruction(tmp_path: Path) -> None:
    refs = [_ref(tmp_path, "azhou.png", material_id="cast:azhou", name="阿舟")]
    prompt = compose_shot_prompt(
        _drama_shot(
            action="他看向门口",
            camera="镜头缓慢推近到胸口，机位稳定",
            dialogue=[],
        ),
        refs,
    )
    assert "竖屏中景" in prompt
    assert "镜头1：缓慢推近到胸口。他看向门口。" in prompt


def test_tutorial_uses_critical_detail_not_purpose(tmp_path: Path) -> None:
    refs = [_ref(tmp_path, "dough.png", material_id="prop:dough", name="面团")]
    prompt = compose_shot_prompt(_tutorial_shot(), refs)
    assert "画面无任何字幕。" in prompt
    assert "将图片1定义为面团。" in prompt
    assert "9:16 竖屏特写，真实室内光线，手机直出。" in prompt
    assert "双手把面团揉圆" in prompt
    assert "面团揉光滑" not in prompt
    assert "dough" not in prompt
    assert "<现场操作声>" in prompt
    assert "画外旁白" not in prompt


def test_tutorial_prompt_does_not_put_narration_into_seedance(tmp_path: Path) -> None:
    refs = [_ref(tmp_path, "dough.png", material_id="prop:dough", name="面团")]
    prompt = compose_shot_prompt(
        _tutorial_shot(narration="松弛后面团更不容易裂。"),
        refs,
    )
    assert "画外旁白" not in prompt
    assert "松弛后面团更不容易裂" not in prompt
    assert "<现场操作声>" in prompt
    assert "开口说" not in prompt
    assert "口型与这句话同步" not in prompt


def test_resolved_drama_refs_compose_without_bind_ids(tmp_path: Path) -> None:
    from tests.test_production_routes import (
        DRAMA_HAPPY_PATH,
        _narrative_run,
        _plant_identity_images,
    )

    narrative_run = _narrative_run("drama", DRAMA_HAPPY_PATH)
    state = narrative_run.record.project_state
    _plant_identity_images(state, tmp_path)
    first = state.production.shots[0]
    materials = shot_materials(state, tmp_path, first.spec.shot_id)
    assert all("身份参考图" not in str(item["default_description"]) for item in materials)
    refs = resolve_shot_references(state, tmp_path, first.spec.shot_id)
    assert refs[0].material_id.startswith(("cast:", "place:", "prop:"))
    assert refs[0].name
    prompt = compose_shot_prompt(first.spec, refs)
    assert "阿舟" in prompt
    assert "老厨房" in prompt
    assert "剧情场景" not in prompt
    assert "scene_attempt" not in prompt
    assert "cast:character_azhou" not in prompt
    assert "location_kitchen" not in prompt


def test_agent_prompt_is_sent_verbatim(tmp_path: Path) -> None:
    refs = [_ref(tmp_path, "azhou.png", material_id="cast:azhou", name="阿舟")]
    text = "9:16 竖屏中景，图片1中的阿舟自己写的句子。"
    assert _shot_prompt(_drama_shot(), refs, text) == text
    assert compose_shot_prompt(_drama_shot(), refs) != text


def test_list_shots_exposes_action_and_identity_names(tmp_path: Path) -> None:
    from tests.test_production_routes import (
        DRAMA_HAPPY_PATH,
        _narrative_run,
        _plant_identity_images,
    )

    narrative_run = _narrative_run("drama", DRAMA_HAPPY_PATH)
    state = narrative_run.record.project_state
    _plant_identity_images(state, tmp_path)
    first = state.production.shots[0]
    payload = _list_shots(state, tmp_path, [first.spec.shot_id], {})
    shot = payload["shots"][0]
    assert shot["visual"]["action_description"] == first.spec.visual.action_description
    assert "generation_prompt" not in shot["visual"]
    names = {item["name"] for item in shot["identities"]}
    assert "阿舟" in names
    assert "老厨房" in names
    assert shot["audio"]["dialogue_lines"] == first.spec.audio.dialogue_lines
    assert "narration_text" in shot["audio"]


def test_generate_shot_video_uses_agent_prompt(tmp_path: Path) -> None:
    from tests.test_production_routes import (
        DRAMA_HAPPY_PATH,
        FakeShotVideoProvider,
        _narrative_run,
        _plant_identity_images,
    )

    narrative_run = _narrative_run("drama", DRAMA_HAPPY_PATH)
    state = narrative_run.record.project_state
    _plant_identity_images(state, tmp_path)
    first = state.production.shots[0]
    provider = FakeShotVideoProvider()
    written = "9:16 竖屏中景，图片1中的阿舟站在图片2的老厨房里。"
    generate_shot_video(
        state,
        tmp_path,
        first.spec.shot_id,
        provider,
        prompt=written,
    )
    assert provider.calls[0]["prompt"] == written


def test_asset_task_can_read_graph_around_a_shot() -> None:
    from tests.test_production_routes import DRAMA_HAPPY_PATH, _narrative_run

    narrative_run = _narrative_run("drama", DRAMA_HAPPY_PATH)
    state = narrative_run.record.project_state
    first = state.production.shots[0]
    around = list_graph(state, around_ref=f"shot:{first.spec.shot_id}")
    assert around["ok"] is True
    refs = {item["ref"] for item in around["nodes"]}
    assert f"shot:{first.spec.shot_id}" in refs
    assert any(item.startswith("cast:") for item in refs)
    task = ShotAssetHarnessController.from_provider(
        object(),  # type: ignore[arg-type]
    ).create_task(narrative_run.artifact, state)
    assert GRAPH_TOOL in task.allowed_tools


def test_identity_task_budget_covers_skill_list_generate_and_submit() -> None:
    from tests.test_production_routes import DRAMA_HAPPY_PATH, _narrative_run
    from ugc_harness.harness.world_identities import bound_identity_refs

    narrative_run = _narrative_run("drama", DRAMA_HAPPY_PATH)
    state = narrative_run.record.project_state
    refs = bound_identity_refs(state)
    assert refs
    task = ShotAssetHarnessController.from_provider(
        object(),  # type: ignore[arg-type]
    ).create_identity_task(narrative_run.artifact, state)
    # skill.activate + list + one generate per identity + submit
    assert task.budget.max_steps >= len(refs) + 3


def test_fill_generation_prompt_does_not_invent_seedance_text() -> None:
    from ugc_harness.agents.narrative_agent.assemble import fill_generation_prompt

    shot = _drama_shot()
    empty = shot.model_copy(
        update={"visual": shot.visual.model_copy(update={"generation_prompt": ""})}
    )
    assert fill_generation_prompt(empty).visual.generation_prompt == ""
    authored = "已经写过的导演卡"
    filled = shot.model_copy(
        update={"visual": shot.visual.model_copy(update={"generation_prompt": authored})}
    )
    assert fill_generation_prompt(filled).visual.generation_prompt == authored


def test_identity_prompt_locks_face_for_cast() -> None:
    from ugc_harness.harness.description import CastMember, WorldPlace, WorldProp
    from ugc_harness.harness.world_identities import identity_prompt

    person = identity_prompt(
        CastMember(
            character_id="character_azhou",
            name="阿舟",
            role="performer",
            description="短发、深色旧棉衣、中等体态",
            appearance_constraints=["始终同一件深色旧棉衣"],
        )
    )
    assert person.startswith("9:16 竖屏锁脸静帧。")
    assert "阿舟" in person
    assert "短发" in person
    assert "肩部以上" in person
    assert "三视图" in person
    assert "Vertical" not in person

    place = identity_prompt(
        WorldPlace(
            place_id="location_kitchen",
            name="老厨房",
            description="旧木桌靠窗、下午侧光、墙边冰箱",
        )
    )
    assert place.startswith("9:16 竖屏场景静帧。")
    assert "老厨房" in place

    prop = identity_prompt(
        WorldProp(
            prop_id="prop_bowl",
            name="青花碗",
            description="圆口、白底青花、中等大小",
        )
    )
    assert prop.startswith("9:16 竖屏道具静帧。")
    assert "青花碗" in prop
