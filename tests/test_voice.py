import io
import wave
from pathlib import Path

from ugc_harness.shared.artifacts import ArtifactWriter
from ugc_harness.agents.narrative_agent import NarrativeArtifact, make_brief
from ugc_harness.agents.narrative_agent.quality import evaluate
from ugc_harness.agents.voice_agent.planning import build_voice_plan
from ugc_harness.agents.voice_agent.tts import (
    NativeWord,
    SynthesisResult,
    _parse_native_words,
)
from ugc_harness.harness.models import (
    ArollCharacter,
    ArollVoiceProfile,
    CriticIssue,
    EvaluationResult,
    ProjectState,
    RuntimeContext,
    VideoState,
)
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
    settings = object()

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
        world_state=narrative.planning.world_state,
        video_profile=narrative.planning.video_profile,
        video=VideoState(
            project_id=narrative.brief.project_id,
            state_version=1,
            narrative_status="passed",
            script_status="passed",
            voice_status="ready",
        ),
    )


def _voice_run(narrative: NarrativeArtifact, project_dir: Path):
    return VoiceHarnessController.from_provider(
        FakeTTS(), "test-voice"
    ).run(narrative, project_dir, _ready_state(narrative))


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
    assert run.record.project_state.video.editorial_status == "ready"
    assert [item.tool for item in run.record.agent_result.actions] == [
        "voice.create_plan",
        "audio.synthesize_narration",
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
        def evaluate(self, artifact, narrative, project_dir, target_ref):
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
    controller = VoiceHarnessController.from_provider(FakeTTS(), "test-voice")
    controller.critic = RejectingCritic()

    run = controller.run(
        narrative,
        tmp_path,
        _ready_state(narrative),
    )

    assert run.record.transition.outcome == "revise"
    assert run.record.transition.to_agent == "voice_agent"
    assert run.record.project_state.video.voice_status == "needs_revision"
    assert run.record.project_state.video.editorial_status == "blocked"


def test_parse_volcengine_native_word_timestamps() -> None:
    frontend = (
        '{"words":[{"word":"测","start_time":10,"end_time":120,'
        '"confidence":0.97}]}'
    )

    words = _parse_native_words(frontend)

    assert words == [NativeWord("测", 10, 120, 0.97)]
