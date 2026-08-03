from __future__ import annotations

from pathlib import Path

from ..agents.voice_agent.models import VoiceArtifact
from ..agents.narrative_agent.models import NarrativeArtifact
from ..harness.models import CriticIssue, EvaluationResult


class VoiceCritic:
    critic_id = "voice_critic"

    def evaluate(
        self,
        artifact: VoiceArtifact,
        narrative: NarrativeArtifact,
        project_dir: str | Path,
        target_ref: str,
    ) -> EvaluationResult:
        root = Path(project_dir)
        audio_path = root / artifact.timed_audio.audio_file
        diagnoses = list(artifact.quality.issues)
        if not audio_path.is_file() or audio_path.stat().st_size <= 44:
            diagnoses.append("完整旁白音频不存在或为空")
        if artifact.quality.segment_coverage < 1:
            diagnoses.append("部分 ScriptSegment 没有对应音频")
        if artifact.quality.realized_beat_coverage < 1:
            diagnoses.append("部分 PlannedBeat 没有生成 RealizedBeat")
        diagnoses = list(dict.fromkeys(diagnoses))
        blocking = [
            item
            for item in diagnoses
            if "不存在" in item or "没有对应音频" in item or "没有生成" in item
        ]
        character = narrative.planning.world_state.aroll_character
        if character is not None:
            speaker = artifact.voice_plan.speaker
            if (
                speaker.character_id != character.character_id
                or speaker.gender != character.voice_profile.gender
                or speaker.age_style != character.voice_profile.age_style
            ):
                message = "TTS speaker identity does not match world-state A-roll character"
                diagnoses.append(message)
                blocking.append(message)
        issues = [
            CriticIssue(
                issue_id=f"{self.critic_id}:{index:03d}",
                critic_id=self.critic_id,
                scope="voice",
                target_ref=target_ref,
                severity="error" if item in blocking else "warning",
                code="VOICE_ARTIFACT_INVALID" if item in blocking else "VOICE_WARNING",
                diagnosis=item,
                repair_options=["retry_voice_segment"],
            )
            for index, item in enumerate(diagnoses, start=1)
        ]
        return EvaluationResult(
            critic_id=self.critic_id,
            target_ref=target_ref,
            passed=not blocking,
            issues=issues,
        )
