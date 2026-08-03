from __future__ import annotations

from .models import EvaluationResult, TransitionRecord


NEXT_AGENT: dict[str, str] = {
    "narrative_agent": "voice_agent",
    "voice_agent": "editorial_agent",
    "editorial_agent": "asset_agent",
    "asset_agent": "timeline_agent",
    "timeline_agent": "render_agent",
    "render_agent": "project_complete",
}


def transition_after_review(
    *,
    current_agent: str,
    evaluation: EvaluationResult,
    committed_state_version: int,
    approved_target: str | None = None,
) -> TransitionRecord:
    """Advance only on an independent final-artifact review pass."""
    if evaluation.passed:
        try:
            target = approved_target or NEXT_AGENT[current_agent]
        except KeyError as exc:
            raise ValueError(
                f"no successor is configured for agent: {current_agent}"
            ) from exc
        return TransitionRecord(
            transition_id=(
                f"transition_{current_agent}_{committed_state_version}_advance"
            ),
            from_agent=current_agent,
            to_agent=target,
            outcome="advance",
            trigger_ref=evaluation.target_ref,
            reason=f"{evaluation.critic_id} approved the final artifact",
            committed_state_version=committed_state_version,
        )
    return TransitionRecord(
        transition_id=f"transition_{current_agent}_{committed_state_version}_revise",
        from_agent=current_agent,
        to_agent=current_agent,
        outcome="revise",
        trigger_ref=evaluation.target_ref,
        reason=f"{evaluation.critic_id} requested artifact revision",
        committed_state_version=committed_state_version,
    )
