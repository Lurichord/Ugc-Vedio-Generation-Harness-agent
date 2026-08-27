"""Intent layer: persistent memory, field catalog, optional MCP tools."""

from .agent import IntentAgent, build_agent_messages
from .brief_sync import HeuristicBriefSync, LlmBriefSync
from .host import OPENING_REPLY, IntentHost, new_session
from .mcp_runtime import IntakeMcpRuntime
from .models import (
    AgentDecision,
    BriefDraft,
    HostResult,
    Inbound,
    IntakeSession,
    IntentDraft,
    ProgressSnapshot,
)
from .tools import INTAKE_MCP_TOOLS
from .view import (
    apply_state_to_session,
    materialize_brief,
    progress_from_video,
)

__all__ = [
    "OPENING_REPLY",
    "AgentDecision",
    "BriefDraft",
    "HeuristicBriefSync",
    "HostResult",
    "INTAKE_MCP_TOOLS",
    "Inbound",
    "IntakeMcpRuntime",
    "IntakeSession",
    "IntentAgent",
    "IntentDraft",
    "IntentHost",
    "LlmBriefSync",
    "ProgressSnapshot",
    "apply_state_to_session",
    "build_agent_messages",
    "materialize_brief",
    "new_session",
    "progress_from_video",
]
