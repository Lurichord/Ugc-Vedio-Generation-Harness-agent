"""Keep memory, then let the agent decide. Host does not route fields."""

from __future__ import annotations

from uuid import uuid4

from .agent import IntentAgent
from .brief_sync import BriefSync, HeuristicBriefSync
from .mcp_runtime import IntakeMcpRuntime
from .models import (
    HostResult,
    Inbound,
    IntakeMessage,
    IntakeSession,
    utc_now,
)
from .view import refresh_session_from_disk

OPENING_REPLY = "你好，想做什么视频？"


def new_session() -> IntakeSession:
    now = utc_now()
    opening = IntakeMessage(role="agent", content=OPENING_REPLY)
    return IntakeSession(
        session_id=f"intake_{uuid4().hex}",
        created_at=now,
        updated_at=now,
        status="waiting_user",
        last_message=OPENING_REPLY,
        messages=[opening],
    )


def append_message(session: IntakeSession, role: str, content: str) -> IntakeSession:
    messages = list(session.messages)
    messages.append(IntakeMessage(role=role, content=content))  # type: ignore[arg-type]
    return session.model_copy(
        update={"messages": messages, "updated_at": utc_now()}
    )


def record_user_text(session: IntakeSession, text: str) -> IntakeSession:
    """Append the user line unless this turn already persisted it."""
    cleaned = _require_user_text(Inbound(text=text))
    if session.messages:
        last = session.messages[-1]
        if last.role == "user" and last.content == cleaned:
            return session
    return append_message(session, "user", cleaned)


class IntentHost:
    def __init__(
        self,
        agent: IntentAgent,
        runtime: IntakeMcpRuntime,
        brief_sync: BriefSync | None = None,
    ) -> None:
        self.agent = agent
        self.runtime = runtime
        self.brief_sync = brief_sync or HeuristicBriefSync()

    def respond(self, session: IntakeSession, inbound: Inbound) -> HostResult:
        text = _require_user_text(inbound)
        session = record_user_text(session, text)
        session = self.brief_sync.apply(session, text)
        session = refresh_session_from_disk(session)
        yielded = self.agent.run_until_yield(session, self.runtime)
        session = yielded.session
        session = append_message(session, "agent", yielded.message)
        session = session.model_copy(
            update={
                "last_message": yielded.message,
                "status": "done" if yielded.done else "waiting_user",
                "updated_at": utc_now(),
            }
        )
        self.runtime.write_session(session)
        return HostResult(
            session=session,
            message=yielded.message,
            done=yielded.done,
        )


def _require_user_text(inbound: Inbound) -> str:
    text = inbound.text.strip()
    if not text:
        raise ValueError("user message cannot be empty")
    return text
