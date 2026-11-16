from langchain.messages import AIMessage, ToolMessage
from langchain_core.language_models.fake_chat_models import (
    FakeMessagesListChatModel,
)

from ugc_agent.agent import build_agent, invoke_turn, on_tool_error, sqlite_checkpointer
from ugc_agent.context import AgentContext
from ugc_agent.store import ProjectStore


def _message_text(result: dict) -> str:
    return "\n".join(
        message.content
        for message in result["messages"]
        if isinstance(getattr(message, "content", None), str)
    )


class ToolCallingFakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def test_create_agent_runs_multiple_steps_and_resumes_thread(tmp_path):
    model = ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "save_outline",
                        "id": "save-outline",
                        "args": {
                            "outline": {
                                "topic": "手冲咖啡",
                                "video_type": "explainer",
                                "audience": "咖啡初学者",
                                "visual_form": "桌面演示",
                            },
                        },
                    }
                ],
            ),
            AIMessage(content="大纲已保存。"),
            AIMessage(content="我记得上一轮对话。"),
        ]
    )
    store = ProjectStore(tmp_path)
    with sqlite_checkpointer(tmp_path / "checkpoints.sqlite") as checkpointer:
        agent = build_agent(model, checkpointer=checkpointer)
        first = invoke_turn(
            agent,
            "做一个手冲咖啡教程",
            thread_id="thread-1",
            context=AgentContext("demo", "turn-1", store),
        )
        second = invoke_turn(
            agent,
            "你还记得主题吗？",
            thread_id="thread-1",
            context=AgentContext("demo", "turn-2", store),
        )

    assert store.load("demo").outline.topic == "手冲咖啡"
    assert len(first["messages"]) == 4
    assert len(second["messages"]) == 6
    assert sum(isinstance(message, ToolMessage) for message in second["messages"]) == 1
    assert "outline" not in second


def test_read_project_returns_saved_state(tmp_path):
    model = ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "save_outline",
                        "id": "save-outline",
                        "args": {
                            "outline": {
                                "topic": "手冲咖啡",
                                "video_type": "explainer",
                                "audience": "咖啡初学者",
                                "visual_form": "桌面演示",
                            },
                        },
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_project",
                        "id": "read-project",
                        "args": {},
                    }
                ],
            ),
            AIMessage(content="已读取。"),
        ]
    )
    store = ProjectStore(tmp_path)
    agent = build_agent(model)
    result = invoke_turn(
        agent,
        "做一个手冲咖啡教程",
        thread_id="thread-1",
        context=AgentContext("demo", "turn-1", store),
    )

    reads = [
        message
        for message in result["messages"]
        if isinstance(message, ToolMessage) and message.name == "read_project"
    ]
    assert len(reads) == 1
    assert "手冲咖啡" in reads[0].content


def test_load_drama_skill_then_writes_outline_without_other_bodies(tmp_path):
    model = ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "load_skill",
                        "id": "load-drama",
                        "args": {"name": "drama"},
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "save_outline",
                        "id": "save-outline",
                        "args": {
                            "outline": {
                                "topic": "雨夜重逢",
                                "video_type": "drama",
                                "audience": "短剧用户",
                                "visual_form": "电影感对白",
                            },
                        },
                    }
                ],
            ),
            AIMessage(content="大纲已保存。"),
        ]
    )
    store = ProjectStore(tmp_path)
    result = invoke_turn(
        build_agent(model),
        "做一条短剧",
        thread_id="thread-1",
        context=AgentContext("demo", "turn-1", store),
    )
    text = _message_text(result)

    assert store.load("demo").outline.video_type == "drama"
    assert "先有完整故事，再拆镜头" in text
    assert "禁止把全部历史视频传给当前段" not in text
    assert "不得为了补字段而编造用户没给的卖点或参考素材" not in text
    assert "这不是数字人口播" not in text


def test_tool_execution_error_becomes_tool_message(tmp_path):
    model = ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "extract-video-frame",
                        "id": "frame-1",
                        "args": {"video_path": "missing.mp4"},
                    }
                ],
            ),
            AIMessage(content="尾帧没抽成。"),
        ]
    )
    store = ProjectStore(tmp_path)
    result = invoke_turn(
        build_agent(model),
        "抽一下尾帧",
        thread_id="thread-1",
        context=AgentContext("demo", "turn-1", store),
    )
    failed = [
        message
        for message in result["messages"]
        if isinstance(message, ToolMessage) and message.name == "extract-video-frame"
    ]
    assert len(failed) == 1
    assert failed[0].status == "error"
    assert "FileNotFoundError" in failed[0].content
    assert "missing.mp4" in failed[0].content
    assert result["messages"][-1].content == "尾帧没抽成。"


def test_on_tool_error_names_tool_and_exception():
    class Request:
        tool_call = {"name": "extract-video-frame"}

    text = on_tool_error(RuntimeError("ffmpeg did not write frame at 12.046s"), Request())
    assert "`extract-video-frame` 失败：RuntimeError: ffmpeg did not write frame at 12.046s。" in text
