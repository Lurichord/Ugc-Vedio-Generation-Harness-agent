import base64
import io
import json
import wave
from pathlib import Path
from types import SimpleNamespace

import httpx

from ugc_harness.shared.artifacts import ArtifactWriter
from ugc_harness.agents.narrative_agent import NarrativeArtifact, make_brief
from ugc_harness.agents.narrative_agent.quality import evaluate
from ugc_harness.agents.voice_agent.capabilities import VoiceCapabilities
from ugc_harness.agents.voice_agent.planning import build_voice_plan
from ugc_harness.agents.voice_agent.tools import VoiceToolSession, voice_task_context
from ugc_harness.agents.voice_agent.tts import (
    NativeWord,
    SynthesisResult,
    VolcengineTTS,
    _parse_native_words,
    _speech_rate,
)
from ugc_harness.content import ArollCharacter, ArollVoiceProfile
from ugc_harness.harness.models import (
    CriticIssue,
    EvaluationResult,
    ProjectState,
    RuntimeContext,
    VideoState,
)
from ugc_harness.agents.generic import StepDecision
from ugc_harness.shared.settings import TTSSettings
from ugc_harness.harness.voice_controller import VoiceHarnessController
from tests.test_quality import sample_plan, sample_script


def _wav_bytes(duration_ms: int = 500, sample_rate: int = 24_000) -> bytes:
    stream = io.BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(bytes(round(sample_rate * duration_ms / 1000) * 2))
    return stream.getvalue()


class FakeTTS:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(voice_id="unused-default")

    def synthesize(
        self,
        *,
        text: str,
        speed_ratio: float,
        output_path: str | Path,
    ) -> SynthesisResult:
        data = _wav_bytes()
        Path(output_path).write_bytes(data)
        return SynthesisResult(
            request_id="request-test",
            log_id="log-test",
            duration_ms=500,
            audio_bytes=data,
            words=[
                NativeWord(text[0], 0, 200, 0.99),
                NativeWord(text[-1], 250, 480, 0.98),
            ],
        )


class VoiceToolModel:
    """Deterministic fixture walking the voice happy path in order."""

    SEQUENCE = [
        "voice.create_plan",
        "audio.synthesize_narration",
        "voice.submit_candidate",
    ]

    def __init__(self) -> None:
        self.selected: list[str] = []

    def decide(self, messages, tools):
        del messages, tools
        name = self.SEQUENCE[len(self.selected) % len(self.SEQUENCE)]
        self.selected.append(name)
        return StepDecision(tool=name)


class CatalogVoiceToolModel(VoiceToolModel):
    SEQUENCE = [
        "voice.select_voice",
        "voice.create_plan",
        "audio.synthesize_narration",
        "voice.submit_candidate",
    ]


def make_voice_controller() -> VoiceHarnessController:
    return VoiceHarnessController.from_provider(
        FakeTTS(),
        "test-voice",
        tool_model=VoiceToolModel(),
    )


def _narrative() -> NarrativeArtifact:
    brief = make_brief(topic="测试语音阶段", duration_seconds=90)
    plan = sample_plan()
    script = sample_script(plan)
    return NarrativeArtifact(
        model="fake-model",
        brief=brief,
        planning=plan,
        script=script,
        quality=evaluate(brief, plan, script),
    )


def test_voice_plan_uses_role_and_brief_tone_for_speed() -> None:
    narrative = _narrative()
    voice_plan = build_voice_plan(
        narrative.brief,
        narrative.script,
        voice_id="test-voice",
    )

    assert len(voice_plan.segments) == len(narrative.script.segments)
    assert voice_plan.segments[0].speed_ratio > voice_plan.segments[3].speed_ratio
    assert voice_plan.segments[0].planned_beat_id == "pb01"


def test_world_character_drives_voice_identity_and_gender_voice() -> None:
    narrative = _narrative()
    character = ArollCharacter(
        character_id="host_main",
        visual_description="30-year-old female culture creator",
        voice_profile=ArollVoiceProfile(
            gender="female",
            age_style="young",
            tone="warm and energetic",
            pace="natural",
        ),
    )
    plan = build_voice_plan(
        narrative.brief,
        narrative.script,
        voice_id="female-test-voice",
        character=character,
    )
    settings = TTSSettings(
        api_key="test-key-123",
        male_voice_id="male-test-voice",
        female_voice_id="female-test-voice",
        neutral_voice_id="neutral-test-voice",
    )

    assert settings.voice_for_gender(character.voice_profile.gender) == plan.speaker.voice_id
    assert plan.speaker.character_id == character.character_id
    assert plan.speaker.gender == "female"
    assert plan.speaker.age_style == "young"


def _ready_state(narrative: NarrativeArtifact) -> ProjectState:
    return ProjectState(
        runtime_context=RuntimeContext(),
        video=VideoState(
            project_id=narrative.brief.project_id,
            state_version=1,
            narrative_status="passed",
            script_status="passed",
            voice_status="ready",
        ),
    )


def _voice_run(narrative: NarrativeArtifact, project_dir: Path):
    return make_voice_controller().run(
        narrative, project_dir, _ready_state(narrative)
    )


def test_voice_agent_creates_audio_alignment_and_realized_beats(
    tmp_path: Path,
) -> None:
    narrative = _narrative()
    project_dir = tmp_path / "测试项目"
    project_dir.mkdir()
    run = _voice_run(narrative, project_dir)
    artifact = run.artifact
    written = ArtifactWriter(tmp_path).write_voice(
        project_dir,
        artifact,
    )

    assert artifact.quality.passed is True
    assert artifact.word_alignment.coverage == 1.0
    assert len(artifact.realized_beats) == len(narrative.planning.beats)
    assert (project_dir / "audio" / "narration.wav").is_file()
    assert (project_dir / "07_voice_plan.json").is_file()
    assert (project_dir / "voice_artifact.json").is_file()
    assert run.record.transition.to_agent == "editorial_agent"
    assert run.record.project_state.evaluation.stages.editorial == "ready"
    assert [item.tool for item in run.record.agent_result.actions] == [
        "voice.create_plan",
        "audio.synthesize_narration",
        "voice.submit_candidate",
    ]
    harness_files = ArtifactWriter(tmp_path).write_voice_run(
        project_dir, run.record
    )
    assert {item.name for item in harness_files} == {
        "voice_task.json",
        "voice_agent_result.json",
        "voice_evaluation.json",
        "voice_transition.json",
        "project_state.json",
        "manifest.json",
    }
    assert any(path.name == "manifest.json" for path in written)


def test_voice_review_failure_does_not_open_editorial_agent(
    tmp_path: Path,
) -> None:
    class RejectingCritic:
        def evaluate(self, artifact, narrative, project_dir, target_ref, **kwargs):
            return EvaluationResult(
                critic_id="voice_critic",
                target_ref=target_ref,
                passed=False,
                issues=[
                    CriticIssue(
                        issue_id="voice_critic:001",
                        critic_id="voice_critic",
                        scope="voice",
                        target_ref=target_ref,
                        severity="error",
                        code="VOICE_REJECTED",
                        diagnosis="测试拒绝",
                    )
                ],
            )

    narrative = _narrative()
    controller = make_voice_controller()
    controller.critic = RejectingCritic()

    run = controller.run(
        narrative,
        tmp_path,
        _ready_state(narrative),
    )

    assert run.record.transition.outcome == "revise"
    assert run.record.transition.to_agent == "voice_agent"
    assert run.record.project_state.evaluation.stages.voice == "needs_revision"
    assert run.record.project_state.evaluation.stages.editorial == "blocked"


def test_parse_volcengine_native_word_timestamps() -> None:
    frontend = (
        '{"words":[{"word":"测","startTime":0.01,"endTime":0.12,'
        '"confidence":0.97}]}'
    )

    words = _parse_native_words(frontend)

    assert words == [NativeWord("测", 10, 120, 0.97)]


def test_speech_rate_maps_speed_ratio_to_v3_scale() -> None:
    assert _speech_rate(1.0) == 0
    assert _speech_rate(1.2) == 20
    assert _speech_rate(0.5) == -50
    assert _speech_rate(2.0) == 100


def test_volcengine_tts_uses_v3_unidirectional_protocol(tmp_path: Path) -> None:
    pcm = bytes(2400)
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["payload"] = json.loads(request.content)
        body = "\n".join(
            [
                json.dumps(
                    {
                        "code": 0,
                        "message": "",
                        "data": base64.b64encode(pcm).decode("ascii"),
                    }
                ),
                json.dumps(
                    {
                        "code": 0,
                        "message": "",
                        "data": None,
                        "sentence": {
                            "words": [
                                {
                                    "word": "中",
                                    "startTime": 0.0,
                                    "endTime": 0.08,
                                    "confidence": 0.9,
                                }
                            ]
                        },
                    }
                ),
                json.dumps({"code": 20000000, "message": "ok", "data": None}),
            ]
        )
        return httpx.Response(200, text=body + "\n", headers={"X-Tt-Logid": "log-v3"})

    settings = TTSSettings(
        api_key="test-key-123",
        voice_id="zh_female_vv_uranus_bigtts",
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    output = tmp_path / "clip.wav"
    with VolcengineTTS(settings, client=client) as tts:
        result = tts.synthesize(
            text="中秋",
            speed_ratio=1.2,
            output_path=output,
            delivery_instruction="你可以用特别特别高兴的语气说话吗？",
            energy="high",
        )

    payload = captured["payload"]
    assert str(captured["url"]).endswith("/api/v3/tts/unidirectional")
    params = payload["req_params"]
    assert params["speaker"] == "zh_female_vv_uranus_bigtts"
    assert params["audio_params"]["format"] == "pcm"
    assert params["audio_params"]["speech_rate"] == 20
    assert params["audio_params"]["loudness_rate"] == 85
    assert params["audio_params"]["enable_subtitle"] is True
    additions = json.loads(params["additions"])
    assert additions["context_texts"] == ["你可以用特别特别高兴的语气说话吗？"]
    assert result.words == [NativeWord("中", 0, 80, 0.9)]
    assert output.is_file()
    assert output.read_bytes().startswith(b"RIFF")


def test_volcengine_tts_surfaces_nested_resource_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            403,
            text=json.dumps(
                {
                    "header": {
                        "code": 45000030,
                        "message": "[resource_id=volc.seedtts.default] requested resource not granted",
                    }
                }
            ),
        )

    settings = TTSSettings(api_key="test-key-123")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    with VolcengineTTS(settings, client=client) as tts:
        try:
            tts.synthesize(text="中秋", speed_ratio=1.0, output_path=tmp_path / "x.wav")
        except RuntimeError as exc:
            assert "45000030" in str(exc)
            assert "requested resource not granted" in str(exc)
        else:
            raise AssertionError("expected TTS failure")


class RecordingPicker:
    def __init__(self, voice_id: str | None = None) -> None:
        self.voice_id = voice_id
        self.candidates: list = []

    def pick(self, *, brief, character, candidates, instruction=None):
        del brief, character, instruction
        self.candidates = list(candidates)
        return self.voice_id or candidates[0].voice_id


def _with_character(
    narrative: NarrativeArtifact, character: ArollCharacter
) -> NarrativeArtifact:
    payload = narrative.planning.model_dump()
    payload["world_state"]["aroll_character"] = character.model_dump()
    payload["video_profile"].update(
        {
            "resolved": "a_roll",
            "character_consistency_required": True,
            "character_id": character.character_id,
            "character_description": character.visual_description,
            "speaker_presence_ratio_min": 0.35,
            "speaker_presence_ratio_max": 0.85,
        }
    )
    return narrative.model_copy(
        update={"planning": type(narrative.planning).model_validate(payload)}
    )


def test_voice_tool_session_selects_catalog_voice_without_harness() -> None:
    character = ArollCharacter(
        character_id="host_main",
        visual_description="30-year-old female culture creator",
        voice_profile=ArollVoiceProfile(
            gender="female",
            age_style="young",
            tone="warm and energetic",
            pace="natural",
        ),
    )
    narrative = _with_character(_narrative(), character)
    picker = RecordingPicker("zh_female_xiaohe_uranus_bigtts")
    provider = FakeTTS()
    session = VoiceToolSession(
        VoiceCapabilities(provider),
        narrative,
        ".",
        picker=picker,
    )

    try:
        session.create_plan()
    except RuntimeError as exc:
        assert "voice.select_voice must succeed first" in str(exc)
    else:
        raise AssertionError("create_plan should require select_voice")

    choice = session.select_voice()
    plan = session.create_plan()

    assert choice.voice_id == "zh_female_xiaohe_uranus_bigtts"
    assert plan.speaker.voice_id == choice.voice_id
    assert provider.settings.voice_id == choice.voice_id
    assert picker.candidates
    assert {item.gender for item in picker.candidates} == {"female"}


def test_voice_agent_selects_catalog_voice_for_female_host(tmp_path: Path) -> None:
    character = ArollCharacter(
        character_id="host_main",
        visual_description="30-year-old female culture creator",
        voice_profile=ArollVoiceProfile(
            gender="female",
            age_style="young",
            tone="warm and energetic",
            pace="natural",
        ),
    )
    narrative = _with_character(_narrative(), character)
    picker = RecordingPicker("zh_female_xiaohe_uranus_bigtts")
    provider = FakeTTS()
    run = VoiceHarnessController.from_provider(
        provider,
        tool_model=CatalogVoiceToolModel(),
        voice_picker=picker,
    ).run(narrative, tmp_path, _ready_state(narrative))

    assert [item.tool for item in run.record.agent_result.actions] == [
        "voice.select_voice",
        "voice.create_plan",
        "audio.synthesize_narration",
        "voice.submit_candidate",
    ]
    assert run.artifact.voice_plan.speaker.voice_id == "zh_female_xiaohe_uranus_bigtts"
    assert provider.settings.voice_id == "zh_female_xiaohe_uranus_bigtts"
    assert run.record.project_state.context.available_models["tts"] == [
        "zh_female_xiaohe_uranus_bigtts"
    ]
    assert picker.candidates
    assert {item.gender for item in picker.candidates} == {"female"}
    assert {item.age_style for item in picker.candidates} == {"young"}


def test_explicit_voice_id_skips_catalog_picker(tmp_path: Path) -> None:
    picker = RecordingPicker("zh_female_xiaohe_uranus_bigtts")
    provider = FakeTTS()
    narrative = _narrative()
    run = VoiceHarnessController.from_provider(
        provider,
        "forced-voice",
        tool_model=VoiceToolModel(),
        voice_picker=picker,
    ).run(narrative, tmp_path, _ready_state(narrative))

    assert run.artifact.voice_plan.speaker.voice_id == "forced-voice"
    assert picker.candidates == []
    assert provider.settings.voice_id == "forced-voice"


def test_create_plan_requires_select_voice_without_override(tmp_path: Path) -> None:
    narrative = _narrative()
    controller = VoiceHarnessController.from_provider(
        FakeTTS(),
        tool_model=VoiceToolModel(),
        voice_picker=RecordingPicker(),
    )
    task = controller.create_task(narrative, _ready_state(narrative))
    execution = controller.agent.run(
        task, narrative=narrative, project_dir=tmp_path
    )

    assert execution.result.status != "completed"
    assert any(
        item.tool == "voice.create_plan"
        and item.result == "failed"
        and "voice.select_voice must succeed first" in (item.reason or "")
        for item in execution.result.actions
    )


def test_voice_task_exposes_select_voice_tool() -> None:
    narrative = _narrative()
    task = make_voice_controller().create_task(narrative, _ready_state(narrative))

    assert task.allowed_tools == [
        "voice.select_voice",
        "voice.create_plan",
        "audio.synthesize_narration",
        "voice.submit_candidate",
    ]
    assert task.budget.max_steps == 8


def test_voice_repair_context_carries_instruction_and_previous_plan() -> None:
    narrative = _narrative()
    plan = build_voice_plan(
        narrative.brief, narrative.script, voice_id="old-voice"
    )

    class Current:
        voice_plan = plan

    payload = voice_task_context(
        narrative,
        instruction="把说话人改成男性",
        current_voice=Current(),
    )

    assert payload["instruction"] == "把说话人改成男性"
    assert payload["current_voice"]["speaker"]["voice_id"] == "old-voice"
    assert payload["voice_context"]["voice_id"] == "old-voice"


def test_voice_session_seeds_previous_voice_on_repair() -> None:
    narrative = _narrative()
    plan = build_voice_plan(
        narrative.brief, narrative.script, voice_id="old-voice"
    )

    class Current:
        voice_plan = plan

    provider = FakeTTS()
    session = VoiceToolSession(
        VoiceCapabilities(provider),
        narrative,
        ".",
        instruction="只重配这一句，不改词",
        current_voice=Current(),
    )
    rebuilt = session.create_plan()

    assert session.selected_voice_id == "old-voice"
    assert rebuilt.speaker.voice_id == "old-voice"
    assert provider.settings.voice_id == "old-voice"


def test_tutorial_voice_plan_uses_explanation_ids() -> None:
    from tests.test_description import _tutorial_artifact

    narrative = _tutorial_artifact()
    plan = build_voice_plan(
        narrative.brief,
        narrative.script,
        voice_id="test-voice",
    )
    first = narrative.script.segments[0]
    assert plan.segments[0].script_segment_id == first.explanation_segment_id
    assert plan.segments[0].planned_beat_id == first.step_id
    assert plan.segments[0].speech_act == "explanation"
    assert {item.script_segment_id for item in plan.segments} == {
        item.explanation_segment_id for item in narrative.script.segments
    }


def test_tutorial_voice_agent_covers_explained_steps(tmp_path: Path) -> None:
    from tests.test_description import _tutorial_artifact

    narrative = _tutorial_artifact()
    project_dir = tmp_path / "教程配音"
    project_dir.mkdir()
    run = _voice_run(narrative, project_dir)
    explained = {item.step_id for item in narrative.script.segments}
    assert run.record.evaluation.passed is True
    assert run.record.transition.to_agent == "timeline_agent"
    assert run.record.project_state.evaluation.stages.editorial == "not_required"
    assert run.record.project_state.evaluation.stages.timeline == "ready"
    assert {beat.planned_beat_id for beat in run.artifact.realized_beats} == explained
    assert (project_dir / "audio" / "narration.wav").is_file()


def test_overlay_wavs_places_segment_after_silence(tmp_path: Path) -> None:
    from ugc_harness.agents.voice_agent.audio import inspect_wav, overlay_wavs

    first = tmp_path / "a.wav"
    second = tmp_path / "b.wav"
    first.write_bytes(_wav_bytes(200))
    second.write_bytes(_wav_bytes(200))
    dest = tmp_path / "mix.wav"
    info = overlay_wavs([(first, 0), (second, 400)], 800, dest)
    assert info.duration_ms == 800
    assert dest.is_file()
    assert inspect_wav(dest).duration_ms == 800


def test_tutorial_narration_starts_follow_placement() -> None:
    from tests.test_description import _tutorial_artifact
    from ugc_harness.agents.voice_agent.models import AudioSegment
    from ugc_harness.harness.shot_render_controller import tutorial_narration_starts
    from ugc_harness.harness.shot_timeline_controller import (
        ShotTimelineArtifact,
        ShotTimelineClip,
    )

    narrative = _tutorial_artifact()
    clips = []
    cursor = 0
    for shot in narrative.shots.shots:
        end = cursor + 4000
        clips.append(
            ShotTimelineClip(
                clip_id=f"clip_{shot.shot_id}",
                shot_id=shot.shot_id,
                start_ms=cursor,
                end_ms=end,
                playback_path=f"assets/{shot.shot_id}.mp4",
                audio_mode="mixed",
                preserve_source_audio=True,
            )
        )
        cursor = end
    timeline = ShotTimelineArtifact(
        project_id=narrative.brief.project_id,
        format_id="tutorial",
        duration_ms=cursor,
        clips=clips,
    )
    segments = []
    for index, item in enumerate(narrative.script.segments, start=1):
        segments.append(
            AudioSegment(
                audio_segment_id=f"as{index:02d}",
                voice_segment_id=f"vs{index:02d}",
                script_segment_id=item.explanation_segment_id,
                planned_beat_id=item.step_id,
                file=f"audio/segments/vs{index:02d}.wav",
                start_ms=0,
                end_ms=500,
                duration_ms=500,
                pause_before_ms=0,
                pause_after_ms=0,
                provider_request_id="t",
            )
        )

    class _Voice:
        timed_audio = type("Timed", (), {"segments": segments})()

    placed = {
        segment.script_segment_id: start
        for segment, start in tutorial_narration_starts(
            narrative, timeline, _Voice()
        )
    }
    first_clip: dict[str, ShotTimelineClip] = {}
    shots = {shot.shot_id: shot for shot in narrative.shots.shots}
    for clip in timeline.clips:
        step_id = shots[clip.shot_id].visual.step_id
        first_clip.setdefault(step_id, clip)
    for item in narrative.script.segments:
        clip = first_clip[item.step_id]
        start = placed[item.explanation_segment_id]
        if item.placement == "after_action":
            assert start == max(clip.start_ms, clip.end_ms - 500)
        else:
            assert start == clip.start_ms

