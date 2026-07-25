from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

from .audio_utils import concatenate_wavs, inspect_wav
from .models import StageOneArtifact
from .tts import NativeWord, SynthesisResult
from .voice_models import (
    AudioSegment,
    RealizedBeat,
    TimedAudio,
    VoiceStageArtifact,
    VoiceStageQuality,
    WordAlignment,
    WordTimestamp,
)
from .voice_plan import build_voice_plan


class TTSProvider(Protocol):
    settings: object

    def synthesize(
        self,
        *,
        text: str,
        speed_ratio: float,
        output_path: str | Path,
    ) -> SynthesisResult: ...


class VoiceStagePipeline:
    def __init__(self, provider: TTSProvider, voice_id: str):
        self.provider = provider
        self.voice_id = voice_id

    def run(
        self,
        stage_one: StageOneArtifact,
        project_dir: str | Path,
    ) -> VoiceStageArtifact:
        root = Path(project_dir)
        audio_dir = root / "audio"
        segment_dir = audio_dir / "segments"
        segment_dir.mkdir(parents=True, exist_ok=True)

        voice_plan = build_voice_plan(
            stage_one.brief,
            stage_one.script,
            voice_id=self.voice_id,
        )
        scripts = {
            segment.script_segment_id: segment
            for segment in stage_one.script.segments
        }

        cursor_ms = 0
        audio_segments: list[AudioSegment] = []
        global_words: list[WordTimestamp] = []
        wav_parts: list[tuple[Path, int, int]] = []
        aligned_segment_count = 0
        word_counter = 1

        for index, segment_plan in enumerate(voice_plan.segments, start=1):
            script = scripts[segment_plan.script_segment_id]
            segment_path = segment_dir / f"{segment_plan.voice_segment_id}.wav"
            result = self.provider.synthesize(
                text=script.text,
                speed_ratio=segment_plan.speed_ratio,
                output_path=segment_path,
            )
            wav_info = inspect_wav(segment_path)
            spoken_start_ms = cursor_ms + segment_plan.pause_before_ms
            spoken_end_ms = spoken_start_ms + wav_info.duration_ms
            audio_segment_id = f"as{index:02d}"
            audio_segments.append(
                AudioSegment(
                    audio_segment_id=audio_segment_id,
                    voice_segment_id=segment_plan.voice_segment_id,
                    script_segment_id=segment_plan.script_segment_id,
                    planned_beat_id=segment_plan.planned_beat_id,
                    file=segment_path.relative_to(root).as_posix(),
                    start_ms=spoken_start_ms,
                    end_ms=spoken_end_ms,
                    duration_ms=wav_info.duration_ms,
                    pause_before_ms=segment_plan.pause_before_ms,
                    pause_after_ms=segment_plan.pause_after_ms,
                    provider_request_id=result.request_id,
                    provider_log_id=result.log_id,
                )
            )
            native_words = result.words or _fallback_words(
                script.text, wav_info.duration_ms
            )
            if result.words:
                aligned_segment_count += 1
            for native in native_words:
                global_words.append(
                    WordTimestamp(
                        word_id=f"w{word_counter:04d}",
                        word=native.word,
                        start_ms=spoken_start_ms + native.start_ms,
                        end_ms=spoken_start_ms + native.end_ms,
                        confidence=native.confidence,
                        script_segment_id=segment_plan.script_segment_id,
                        planned_beat_id=segment_plan.planned_beat_id,
                    )
                )
                word_counter += 1
            wav_parts.append(
                (
                    segment_path,
                    segment_plan.pause_before_ms,
                    segment_plan.pause_after_ms,
                )
            )
            cursor_ms = spoken_end_ms + segment_plan.pause_after_ms

        narration_path = audio_dir / "narration.wav"
        final_info = concatenate_wavs(wav_parts, narration_path)
        timed_audio = TimedAudio(
            audio_file=narration_path.relative_to(root).as_posix(),
            duration_ms=final_info.duration_ms,
            sample_rate=final_info.sample_rate,
            channels=final_info.channels,
            sample_width_bytes=final_info.sample_width_bytes,
            segments=audio_segments,
        )
        alignment = WordAlignment(
            source_audio=timed_audio.audio_file,
            normalized_text="".join(word.word for word in global_words),
            word_count=len(global_words),
            words=global_words,
            aligned_segment_count=aligned_segment_count,
            total_segment_count=len(voice_plan.segments),
            coverage=round(
                aligned_segment_count / len(voice_plan.segments), 4
            ),
        )
        realized_beats = _build_realized_beats(
            stage_one, audio_segments, global_words
        )
        quality = _evaluate_voice_stage(
            stage_one,
            narration_path,
            timed_audio,
            alignment,
            realized_beats,
        )
        return VoiceStageArtifact(
            project_id=stage_one.brief.project_id,
            source_stage_one="stage_one_artifact.json",
            voice_plan=voice_plan,
            timed_audio=timed_audio,
            word_alignment=alignment,
            realized_beats=realized_beats,
            quality=quality,
        )


def _fallback_words(text: str, duration_ms: int) -> list[NativeWord]:
    tokens = re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9]+", text)
    if not tokens:
        return []
    slice_ms = duration_ms / len(tokens)
    return [
        NativeWord(
            word=token,
            start_ms=round(index * slice_ms),
            end_ms=max(round((index + 1) * slice_ms), round(index * slice_ms) + 1),
            confidence=None,
        )
        for index, token in enumerate(tokens)
    ]


def _build_realized_beats(
    stage_one: StageOneArtifact,
    audio_segments: list[AudioSegment],
    words: list[WordTimestamp],
) -> list[RealizedBeat]:
    scripts_by_beat: dict[str, list] = {}
    for segment in stage_one.script.segments:
        scripts_by_beat.setdefault(segment.planned_beat_id, []).append(segment)
    audio_by_beat: dict[str, list[AudioSegment]] = {}
    for segment in audio_segments:
        audio_by_beat.setdefault(segment.planned_beat_id, []).append(segment)
    words_by_beat: dict[str, list[WordTimestamp]] = {}
    for word in words:
        words_by_beat.setdefault(word.planned_beat_id, []).append(word)

    realized: list[RealizedBeat] = []
    for index, beat in enumerate(stage_one.planning.beats, start=1):
        beat_audio = audio_by_beat.get(beat.planned_beat_id, [])
        beat_scripts = scripts_by_beat.get(beat.planned_beat_id, [])
        if not beat_audio or not beat_scripts:
            continue
        start_ms = min(segment.start_ms for segment in beat_audio)
        end_ms = max(segment.end_ms for segment in beat_audio)
        realized.append(
            RealizedBeat(
                beat_id=f"b{index:02d}",
                planned_beat_id=beat.planned_beat_id,
                start_ms=start_ms,
                end_ms=end_ms,
                duration_ms=end_ms - start_ms,
                script_segment_ids=[
                    segment.script_segment_id for segment in beat_scripts
                ],
                audio_segment_ids=[
                    segment.audio_segment_id for segment in beat_audio
                ],
                narration="".join(segment.text for segment in beat_scripts),
                proposition=beat.semantic_goal,
                discourse_role=beat.discourse_role,
                relation_to_previous=beat.relation_to_previous,
                word_ids=[
                    word.word_id
                    for word in words_by_beat.get(beat.planned_beat_id, [])
                ],
            )
        )
    return realized


def _evaluate_voice_stage(
    stage_one: StageOneArtifact,
    narration_path: Path,
    timed_audio: TimedAudio,
    alignment: WordAlignment,
    realized_beats: list[RealizedBeat],
) -> VoiceStageQuality:
    issues: list[str] = []
    segment_coverage = len(timed_audio.segments) / len(stage_one.script.segments)
    beat_coverage = len(realized_beats) / len(stage_one.planning.beats)
    if not narration_path.is_file() or narration_path.stat().st_size <= 44:
        issues.append("完整旁白音频不存在或为空")
    if segment_coverage < 1:
        issues.append("部分 ScriptSegment 没有对应音频")
    if alignment.coverage < 1:
        issues.append("部分音频段缺少服务端原生时间戳，已使用比例回退对齐")
    if beat_coverage < 1:
        issues.append("部分 PlannedBeat 没有生成 RealizedBeat")
    return VoiceStageQuality(
        passed=not any(
            issue
            for issue in issues
            if "不存在" in issue or "没有对应音频" in issue or "没有生成" in issue
        ),
        audio_exists=narration_path.is_file(),
        audio_duration_ms=timed_audio.duration_ms,
        segment_coverage=round(segment_coverage, 4),
        word_alignment_coverage=alignment.coverage,
        realized_beat_coverage=round(beat_coverage, 4),
        issues=issues,
    )
