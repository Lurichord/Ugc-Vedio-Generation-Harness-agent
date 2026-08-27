"""ProjectState slices that belong in intake memory. No shot lists leave here."""

from __future__ import annotations

from pathlib import Path

from ..agents.narrative_agent.brief import make_brief
from ..agents.narrative_agent.models import CreativeBrief
from ..content import AudienceSpec, ContentPolicy, TargetSpec
from ..harness.description import VideoIntent
from ..harness.models import ProjectState, VideoState
from .models import BriefDraft, IntakeSession, IntentDraft, ProgressSnapshot, utc_now


def progress_from_video(video: VideoState) -> ProgressSnapshot:
    return ProgressSnapshot(
        project_id=video.project_id,
        state_version=video.state_version,
        narrative=video.narrative_status,
        script=video.script_status,
        voice=video.voice_status,
        editorial=video.editorial_status,
        asset=video.asset_status,
        timeline=video.timeline_status,
        render=video.render_status,
    )


def brief_draft_from_creative_brief(brief: CreativeBrief) -> BriefDraft:
    return BriefDraft.model_validate(brief.model_dump())


def intent_draft_from_video_intent(intent: VideoIntent) -> IntentDraft:
    return IntentDraft.model_validate(intent.model_dump())


def load_project_state(project_dir: Path) -> ProjectState | None:
    path = project_dir / "harness" / "project_state.json"
    if not path.is_file():
        return None
    return ProjectState.model_validate_json(path.read_text(encoding="utf-8"))


def apply_state_to_session(session: IntakeSession, state: ProjectState) -> IntakeSession:
    progress = progress_from_video(state.video)
    updates: dict[str, object] = {
        "progress": progress,
        "project_id": state.video.project_id or session.project_id,
        "updated_at": utc_now(),
    }
    description = state.description
    if description is not None:
        intent = description.intent
        updates["working_intent"] = intent_draft_from_video_intent(intent)
        updates["working_brief"] = _sync_brief_from_intent(session.working_brief, intent)
    return session.model_copy(update=updates)


def refresh_session_from_disk(session: IntakeSession) -> IntakeSession:
    if not session.project_dir:
        return session
    state = load_project_state(Path(session.project_dir))
    if state is None:
        return session
    return apply_state_to_session(session, state)


def materialize_brief(draft: BriefDraft) -> CreativeBrief:
    topic = (draft.topic or "").strip()
    if not topic:
        raise ValueError("CreativeBrief 需要 topic")
    communication = draft.communication
    if communication is None or not communication.goal.strip():
        raise ValueError("CreativeBrief 需要 communication.goal")
    target = draft.target or TargetSpec()
    audience = draft.audience or AudienceSpec()
    brief = make_brief(
        topic=topic,
        project_name=draft.project_name,
        duration_seconds=target.duration_target_ms // 1000,
        platform=target.platform,
        audience=audience.description,
        goal=communication.goal,
        tone=communication.tone,
        creator_persona=communication.creator_persona,
        production_mode=draft.production_mode or "auto",
        video_profile=draft.video_profile or "auto",
    )
    updates: dict[str, object] = {
        "target": target,
        "audience": audience,
        "communication": communication,
        "content_policy": draft.content_policy or ContentPolicy(),
    }
    if draft.project_id:
        updates["project_id"] = draft.project_id
    return brief.model_copy(update=updates)


def format_progress(progress: ProgressSnapshot) -> str:
    return (
        f"project_id={progress.project_id or '无'} v={progress.state_version} "
        f"narrative={progress.narrative} script={progress.script} "
        f"voice={progress.voice} editorial={progress.editorial} "
        f"asset={progress.asset} timeline={progress.timeline} "
        f"render={progress.render}"
    )


def _sync_brief_from_intent(current: BriefDraft, intent: VideoIntent) -> BriefDraft:
    production_mode = (
        intent.format_id
        if intent.format_id in {"explainer", "drama", "tutorial"}
        else current.production_mode
    )
    return current.model_copy(
        update={
            "topic": intent.topic,
            "target": intent.target,
            "audience": intent.audience,
            "communication": intent.communication,
            "content_policy": intent.content_policy,
            "production_mode": production_mode,
        }
    )
