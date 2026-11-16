from langchain.messages import AIMessage
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

from ugc_agent.agent import build_agent, invoke_turn, sqlite_checkpointer
from ugc_agent.context import AgentContext
from ugc_agent.inspect_thread import list_thread_ids, load_thread
from ugc_agent.store import ProjectStore


class ToolCallingFakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def test_inspect_thread_reads_saved_checkpoint(tmp_path):
    db = tmp_path / "checkpoints.sqlite"
    store = ProjectStore(tmp_path)
    with sqlite_checkpointer(db) as checkpointer:
        invoke_turn(
            build_agent(
                ToolCallingFakeModel(responses=[AIMessage(content="先说片种。")]),
                checkpointer=checkpointer,
            ),
            "做一个中秋讲解",
            thread_id="thread-midautumn",
            context=AgentContext("demo", "turn-1", store),
        )

    assert list_thread_ids(db) == ["thread-midautumn"]
    payload = load_thread(db, "thread-midautumn")
    assert payload["thread_id"] == "thread-midautumn"
    assert payload["messages"][0]["type"] == "human"
    assert payload["messages"][0]["content"] == "做一个中秋讲解"
    assert payload["turns"][0]["user"] == "做一个中秋讲解"
