from __future__ import annotations

from typing import Annotated

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain.agents.middleware.types import PrivateStateAttr
from langchain.messages import SystemMessage
from langgraph.channels.untracked_value import UntrackedValue
from langgraph.types import Command
from typing_extensions import NotRequired

from .context import AgentContext

PAUSE_AFTER = {
    "save_outline",
    "save_story",
    "save_shot_design",
    "set_deliverable",
}

PAUSE_HINT = "本 Turn 账本这一步已经写完。必须用一两句中文告诉用户做了什么，不要再调用工具，禁止空回复。"


class StepPauseState(AgentState):
    pause_turn: NotRequired[Annotated[bool, UntrackedValue, PrivateStateAttr]]


class StepPauseMiddleware(AgentMiddleware[StepPauseState, AgentContext]):
    state_schema = StepPauseState

    def wrap_tool_call(self, request, handler):
        result = handler(request)
        if request.tool_call["name"] in PAUSE_AFTER:
            return Command(update={"pause_turn": True, "messages": [result]})
        return result

    def wrap_model_call(self, request, handler):
        if not request.state.get("pause_turn"):
            return handler(request)
        system = SystemMessage(
            content=f"{request.system_message.content}\n\n{PAUSE_HINT}"
        )
        return handler(request.override(system_message=system))
