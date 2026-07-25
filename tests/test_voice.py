import io
import wave
from pathlib import Path

from ugc_harness.artifacts import ArtifactWriter
from ugc_harness.models import StageOneArtifact
from ugc_harness.pipeline import make_brief
from ugc_harness.quality import evaluate
from ugc_harness.tts import NativeWord, SynthesisResult, _parse_native_words
from ugc_harness.voice_pipeline import VoiceStagePipeline
from ugc_harness.voice_plan import build_voice_plan
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


def _stage_one() -> StageOneArtifact:
    brief = make_brief(topic="测试语音阶段", duration_seconds=90)
    plan = sample_plan()
    script = sample_script(plan)
    return StageOneArtifact(
        model="fake-model",
        brief=brief,
        planning=plan,
        script=script,
        quality=evaluate(brief, plan, script),
    )


def test_voice_plan_uses_role_and_brief_tone_for_speed() -> None:
    stage_one = _stage_one()
    voice_plan = build_voice_plan(
        stage_one.brief,
        stage_one.script,
        voice_id="test-voice",
    )

    assert len(voice_plan.segments) == len(stage_one.script.segments)
    assert voice_plan.segments[0].speed_ratio > voice_plan.segments[3].speed_ratio
    assert voice_plan.segments[0].planned_beat_id == "pb01"


def test_voice_pipeline_creates_audio_alignment_and_realized_beats(
    tmp_path: Path,
) -> None:
    stage_one = _stage_one()
    project_dir = tmp_path / "测试项目"
    project_dir.mkdir()
    artifact = VoiceStagePipeline(FakeTTS(), "test-voice").run(
        stage_one,
        project_dir,
    )
    written = ArtifactWriter(tmp_path).write_voice_stage(
        project_dir,
        artifact,
    )

    assert artifact.quality.passed is True
    assert artifact.word_alignment.coverage == 1.0
    assert len(artifact.realized_beats) == len(stage_one.planning.beats)
    assert (project_dir / "audio" / "narration.wav").is_file()
    assert (project_dir / "07_voice_plan.json").is_file()
    assert (project_dir / "stage_two_artifact.json").is_file()
    assert any(path.name == "manifest.json" for path in written)


def test_parse_volcengine_native_word_timestamps() -> None:
    frontend = (
        '{"words":[{"word":"测","start_time":10,"end_time":120,'
        '"confidence":0.97}]}'
    )

    words = _parse_native_words(frontend)

    assert words == [NativeWord("测", 10, 120, 0.97)]
