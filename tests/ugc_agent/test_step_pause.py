from langchain.agents.middleware import ModelRequest
from langchain.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from ugc_agent.step_pause import PAUSE_HINT, StepPauseMiddleware


def test_save_outline_marks_pause_and_next_model_call_gets_hint():
    middleware = StepPauseMiddleware()
    saved = ToolMessage(content="ok", tool_call_id="1", name="save_outline")
    paused = middleware.wrap_tool_call(
        ToolCallRequest(
            tool_call={"name": "save_outline", "id": "1", "args": {}},
            tool=None,
            state={},
            runtime=None,
        ),
        lambda request: saved,
    )
    assert isinstance(paused, Command)
    assert paused.update["pause_turn"] is True
    assert paused.update["messages"] == [saved]

    captured = []

    def handler(request):
        captured.append(request.system_message.content)
        return AIMessage(content="大纲写好了。")

    middleware.wrap_model_call(
        ModelRequest(
            model=FakeMessagesListChatModel(responses=[]),
            messages=[],
            system_message=SystemMessage(content="base"),
            state={"pause_turn": True},
            tools=[],
        ),
        handler,
    )
    assert captured[0] == f"base\n\n{PAUSE_HINT}"


def test_other_tools_do_not_pause():
    middleware = StepPauseMiddleware()
    result = ToolMessage(content="ok", tool_call_id="1", name="read_project")
    returned = middleware.wrap_tool_call(
        ToolCallRequest(
            tool_call={"name": "read_project", "id": "1", "args": {}},
            tool=None,
            state={},
            runtime=None,
        ),
        lambda request: result,
    )
    assert returned is result
