from fastapi.testclient import TestClient
from langchain.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.language_models.fake_chat_models import (
    FakeMessagesListChatModel,
)

from ugc_agent.api.main import create_app
from ugc_agent.api.views import project_chat, project_run
from ugc_agent.state import MediaAsset
from ugc_agent.store import ProjectStore


class ToolCallingFakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def _client(tmp_path, model) -> TestClient:
    app = create_app(
        model=model,
        store_root=tmp_path / "projects",
        checkpoints_path=tmp_path / "checkpoints.sqlite",
    )
    return TestClient(app)


def _outline_model() -> ToolCallingFakeModel:
    return ToolCallingFakeModel(
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
        ]
    )


def test_studio_page_is_served_alongside_api(tmp_path):
    with _client(tmp_path, _outline_model()) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "text/html" in page.headers["content-type"]
        assert "data-pane=\"chat\"" in page.text
        assert "data-pane=\"state\"" in page.text
        assert "data-pane=\"runs\"" in page.text
        assert client.get("/api/health").json() == {"ok": True}
        assert client.get("/styles.css").status_code == 200
        assert client.get("/app.js").status_code == 200


def test_list_projects_skips_invalid_json(tmp_path):
    with _client(tmp_path, _outline_model()) as client:
        good = client.post("/api/projects").json()["project_id"]
        broken = tmp_path / "projects" / "broken" / "project_state.json"
        broken.parent.mkdir(parents=True)
        broken.write_text("{}\nnot-json\n", encoding="utf-8")
        listed = client.get("/api/projects").json()["projects"]
        assert [item["project_id"] for item in listed] == [good]


def test_health_and_create_project(tmp_path):
    with _client(tmp_path, _outline_model()) as client:
        assert client.get("/api/health").json() == {"ok": True}
        created = client.post("/api/projects")
        assert created.status_code == 201
        body = created.json()
        assert body["revision"] == 0
        assert body["topic"] is None
        assert body["has_deliverable"] is False
        project_id = body["project_id"]
        listed = client.get("/api/projects").json()["projects"]
        assert listed[0]["project_id"] == project_id
        assert client.get(f"/api/projects/{project_id}").json()["project_id"] == project_id
        assert client.get("/api/projects/missing").status_code == 404


def test_post_message_updates_state_and_splits_chat_from_run(tmp_path):
    with _client(tmp_path, _outline_model()) as client:
        project_id = client.post("/api/projects").json()["project_id"]
        posted = client.post(
            f"/api/projects/{project_id}/messages",
            json={"content": "做一个手冲咖啡教程"},
        )
        assert posted.status_code == 200
        body = posted.json()
        assert body["message"]["content"] == "大纲已保存。"
        assert body["revision"] == 1
        assert body["turn_id"].startswith("turn_")

        state = client.get(f"/api/projects/{project_id}/state").json()
        assert state["outline"]["topic"] == "手冲咖啡"
        assert state["revision"] == body["revision"]

        messages = client.get(f"/api/projects/{project_id}/messages").json()["messages"]
        assert messages == [
            {"role": "user", "content": "做一个手冲咖啡教程"},
            {"role": "assistant", "content": "大纲已保存。"},
        ]
        assert "save_outline" not in str(messages)

        run = client.get(f"/api/projects/{project_id}/run").json()
        assert len(run["turns"]) == 1
        assert run["turns"][0]["user"] == "做一个手冲咖啡教程"
        assert run["turns"][0]["turn_id"] == body["turn_id"]
        names = [
            call["name"]
            for step in run["turns"][0]["steps"]
            for call in step["tool_calls"]
        ]
        assert "save_outline" in names
        results = [
            result["name"]
            for step in run["turns"][0]["steps"]
            for result in step["tool_results"]
        ]
        assert "save_outline" in results


def test_run_projection_groups_turn_steps():
    messages = [
        HumanMessage(content="做一条短剧", additional_kwargs={"turn_id": "turn_abc"}),
        AIMessage(
            content="",
            tool_calls=[{"name": "load_skill", "id": "1", "args": {"name": "drama"}}],
        ),
        ToolMessage(content="skill body", tool_call_id="1", name="load_skill"),
        AIMessage(content="大纲已保存。"),
    ]
    assert project_chat(messages) == [
        {"role": "user", "content": "做一条短剧"},
        {"role": "assistant", "content": "大纲已保存。"},
    ]
    turns = project_run(messages)
    assert turns[0]["turn_id"] == "turn_abc"
    assert [step["tool_calls"] for step in turns[0]["steps"]] == [
        [{"name": "load_skill", "args": {"name": "drama"}}],
        [],
    ]
    assert turns[0]["steps"][0]["tool_results"] == [{"name": "load_skill", "ok": True}]


def test_media_is_served_from_project_dir(tmp_path):
    model = ToolCallingFakeModel(responses=[AIMessage(content="ok")])
    with _client(tmp_path, model) as client:
        project_id = client.post("/api/projects").json()["project_id"]
        store = ProjectStore(tmp_path / "projects")
        media_dir = tmp_path / "projects" / project_id / "media"
        path = media_dir / "frame.png"
        path.write_bytes(b"png")
        state = store.load(project_id)
        store.save(
            state.model_copy(
                update={
                    "media": {
                        "frame": MediaAsset(
                            media_id="frame",
                            kind="image",
                            path=str(path),
                            owner_ref="project",
                            source="generated",
                            mime_type="image/png",
                        )
                    }
                }
            ),
        )
        response = client.get(f"/api/projects/{project_id}/media/frame")
        assert response.status_code == 200
        assert response.content == b"png"
        assert client.get(f"/api/projects/{project_id}/media/missing").status_code == 404


def test_video_gen_message_pauses_until_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "ugc_agent.tools.video_gen.generate_video",
        lambda prompt, **kwargs: b"mp4-bytes",
    )
    model = ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "video-gen",
                        "id": "vid-1",
                        "args": {
                            "prompt": "数字人口播",
                            "duration_seconds": 8,
                            "mode": "ref2va",
                        },
                    }
                ],
            ),
            AIMessage(content="视频已生成。"),
        ]
    )
    with _client(tmp_path, model) as client:
        project_id = client.post("/api/projects").json()["project_id"]
        posted = client.post(
            f"/api/projects/{project_id}/messages",
            json={"content": "生成第一段"},
        )
        assert posted.status_code == 202
        interrupt = posted.json()["interrupt"]
        assert interrupt["name"] == "video-gen"
        assert interrupt["args"]["mode"] == "ref2va"

        chat = client.get(f"/api/projects/{project_id}/messages").json()
        assert chat["interrupt"]["id"] == interrupt["id"]
        assert chat["messages"][0]["content"] == "生成第一段"

        resumed = client.post(
            f"/api/projects/{project_id}/messages/resume",
            json={
                "interrupt_id": interrupt["id"],
                "decision": {"type": "approve"},
            },
        )
        assert resumed.status_code == 200
        assert resumed.json()["message"]["content"] == "视频已生成。"
        assert client.get(f"/api/projects/{project_id}/messages").json()["interrupt"] is None
