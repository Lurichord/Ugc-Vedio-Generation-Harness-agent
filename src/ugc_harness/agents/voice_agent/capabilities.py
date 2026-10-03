from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

from ...content import ArollCharacter
from .audio import concatenate_wavs, inspect_wav
from ..narrative_agent.models import (
    CreativeBrief,
    NarrativeArtifact,
    ScriptArtifact,
    TutorialPlanningArtifact,
    TutorialScriptArtifact,
)
from .tts import NativeWord, SynthesisResult
from .models import (
    AudioSegment,
    RealizedBeat,
    TimedAudio,
    VoiceArtifact,
    VoicePlan,
    VoiceQuality,
    WordAlignment,
    WordTimestamp,
)
from .voice_select import VoiceChoice, VoicePicker, select_voice


class TTSProvider(Protocol):
    settings: object

    def synthesize(
        self,
        *,
        text: str,
        speed_ratio: float,
        output_path: str | Path,
        delivery_instruction: str | None = None,
        energy: str | None = None,
    ) -> SynthesisResult: ...


class VoiceCapabilities:
    """Deterministic audio operations exposed to VoiceAgent as tools."""

    def __init__(self, provider: TTSProvider):
        self.provider = provider

    def select_narration_voice(
        self,
        brief: CreativeBrief,
        character: ArollCharacter | None,
        *,
        picker: VoicePicker | None = None,
        override_voice_id: str | None = None,
        instruction: str | None = None,
    ) -> VoiceChoice:
        voice_id = select_voice(
            brief,
            character,
            picker=picker,
            override_voice_id=override_voice_id,
            instruction=instruction,
        )
        return VoiceChoice(
            voice_id=voice_id,
            reason="override" if override_voice_id else "catalog",
        )

    def synthesize_narration(
        self,
        narrative: NarrativeArtifact,
        voice_plan: VoicePlan,
        project_dir: str | Path,
    ) -> VoiceArtifact:
        root = Path(project_dir)
        audio_dir = root / "audio"
        segment_dir = audio_dir / "segments"
        segment_dir.mkdir(parents=True, exist_ok=True)

        spoken = _spoken_lines(narrative)

        cursor_ms = 0
        audio_segments: list[AudioSegment] = []
        global_words: list[WordTimestamp] = []
        wav_parts: list[tuple[Path, int, int]] = []
        aligned_segment_count = 0
        word_counter = 1

        for index, segment_plan in enumerate(voice_plan.segments, start=1):
            text, _beat_id = spoken[segment_plan.script_segment_id]
            segment_path = segment_dir / f"{segment_plan.voice_segment_id}.wav"
            result = self.provider.synthesize(
                text=text,
                speed_ratio=segment_plan.speed_ratio,
                output_path=segment_path,
                delivery_instruction=segment_plan.delivery_instruction,
                energy=segment_plan.energy,
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
                text, wav_info.duration_ms
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
            narrative, audio_segments, global_words
        )
        quality = _evaluate_voice_stage(
            narrative,
            narration_path,
            timed_audio,
            alignment,
            realized_beats,
        )
        return VoiceArtifact(
            project_id=narrative.brief.project_id,
            source_narrative="narrative_artifact.json",
            voice_plan=voice_plan,
            timed_audio=timed_audio,
            word_alignment=alignment,
            realized_beats=realized_beats,
            quality=quality,
        )


def _spoken_lines(narrative: NarrativeArtifact) -> dict[str, tuple[str, str]]:
    script = narrative.script
    if isinstance(script, TutorialScriptArtifact):
        return {
            item.explanation_segment_id: (item.text, item.step_id)
            for item in script.segments
        }
    if isinstance(script, ScriptArtifact):
        return {
            item.script_segment_id: (item.text, item.planned_beat_id)
            for item in script.segments
        }
    raise ValueError("voice synthesis requires a spoken script")


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
    narrative: NarrativeArtifact,
    audio_segments: list[AudioSegment],
    words: list[WordTimestamp],
) -> list[RealizedBeat]:
    audio_by_beat: dict[str, list[AudioSegment]] = {}
    for segment in audio_segments:
        audio_by_beat.setdefault(segment.planned_beat_id, []).append(segment)
    words_by_beat: dict[str, list[WordTimestamp]] = {}
    for word in words:
        words_by_beat.setdefault(word.planned_beat_id, []).append(word)

    if isinstance(narrative.planning, TutorialPlanningArtifact):
        texts = {
            item.explanation_segment_id: item.text
            for item in narrative.script.segments
        } if isinstance(narrative.script, TutorialScriptArtifact) else {}
        realized: list[RealizedBeat] = []
        for index, step in enumerate(narrative.planning.steps, start=1):
            beat_audio = audio_by_beat.get(step.step_id, [])
            if not beat_audio:
                continue
            start_ms = min(segment.start_ms for segment in beat_audio)
            end_ms = max(segment.end_ms for segment in beat_audio)
            script_ids = [segment.script_segment_id for segment in beat_audio]
            realized.append(
                RealizedBeat(
                    beat_id=f"b{index:02d}",
                    planned_beat_id=step.step_id,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    duration_ms=end_ms - start_ms,
                    script_segment_ids=script_ids,
                    audio_segment_ids=[
                        segment.audio_segment_id for segment in beat_audio
                    ],
                    narration="".join(texts.get(item, "") for item in script_ids),
                    proposition=step.instruction,
                    discourse_role="explanation",
                    relation_to_previous=(
                        "opening" if index == 1 else "continuation"
                    ),
                    word_ids=[
                        word.word_id
                        for word in words_by_beat.get(step.step_id, [])
                    ],
                )
            )
        return realized

    scripts_by_beat: dict[str, list] = {}
    for segment in narrative.script.segments:
        scripts_by_beat.setdefault(segment.planned_beat_id, []).append(segment)

    realized: list[RealizedBeat] = []
    for index, beat in enumerate(narrative.planning.beats, start=1):
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
    narrative: NarrativeArtifact,
    narration_path: Path,
    timed_audio: TimedAudio,
    alignment: WordAlignment,
    realized_beats: list[RealizedBeat],
) -> VoiceQuality:
    issues: list[str] = []
    script = narrative.script
    spoken_count = len(script.segments) if script is not None else 0
    segment_coverage = (
        len(timed_audio.segments) / spoken_count if spoken_count else 1.0
    )
    if isinstance(narrative.planning, TutorialPlanningArtifact):
        explained = {
            item.step_id for item in script.segments
        } if isinstance(script, TutorialScriptArtifact) else set()
        covered = {item.planned_beat_id for item in realized_beats}
        beat_coverage = len(covered) / len(explained) if explained else 1.0
        missing_audio = "部分讲解段没有对应音频"
        missing_beats = "部分有讲解的步骤没有生成 RealizedBeat"
    else:
        beat_coverage = (
            len(realized_beats) / len(narrative.planning.beats)
            if narrative.planning.beats
            else 1.0
        )
        missing_audio = "部分 ScriptSegment 没有对应音频"
        missing_beats = "部分 PlannedBeat 没有生成 RealizedBeat"
    if not narration_path.is_file() or narration_path.stat().st_size <= 44:
        issues.append("完整旁白音频不存在或为空")
    if segment_coverage < 1:
        issues.append(missing_audio)
    if alignment.coverage < 1:
        issues.append("部分音频段缺少服务端原生时间戳，已使用比例回退对齐")
    if beat_coverage < 1:
        issues.append(missing_beats)
    return VoiceQuality(
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
