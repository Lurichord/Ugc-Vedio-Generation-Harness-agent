import json
from pathlib import Path

import pytest

from langchain.messages import AIMessage, ToolMessage
from langchain_core.language_models.fake_chat_models import (
    FakeMessagesListChatModel,
)

from ugc_agent.agent import build_agent, invoke_turn, resume_turn
from ugc_agent.context import AgentContext
from ugc_agent.store import ProjectStore
from ugc_agent.agent import system_prompt
from ugc_agent.tools.image_gen import image_gen
from ugc_agent.tools.video_gen import video_gen
from ugc_agent.tools.video_gen.client import _content, _duration_seconds
from ugc_agent.tools.video_gen_minimax import video_gen_minimax
from ugc_agent.tools.video_gen_minimax.client import (
    _encode_multipart,
    _form_parts,
    generate_video as generate_minimax_video,
)
from ugc_agent.tools.vision_understanding import vision_understanding


class ToolCallingFakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def _tool_payload(result: dict, name: str) -> dict | str:
    messages = [
        message
        for message in result["messages"]
        if isinstance(message, ToolMessage) and message.name == name
    ]
    assert len(messages) == 1
    content = messages[0].content
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return content


def test_generation_tools_describe_usage_and_parameters():
    assert "人物主锚图" in image_gen.description
    assert "prompt" in image_gen.tool_call_schema.model_json_schema()["properties"]
    assert "fl2va" in video_gen.description
    assert "ref2va" in video_gen.description
    properties = video_gen.tool_call_schema.model_json_schema()["properties"]
    assert "mode" in properties
    assert "audio_paths" in properties
    assert "[图1]" in properties["prompt"]["description"]
    assert "图片≤9" in properties["prompt"]["description"]
    minimax = video_gen_minimax.tool_call_schema.model_json_schema()["properties"]
    assert "h3-prompt" in minimax["prompt"]["description"]
    assert "图1 图2" in minimax["prompt"]["description"]
    assert "video-gen-minimax" in (video_gen_minimax.name or "")
    assert "UGC_LLM_MODEL" in vision_understanding.description
    assert "media_paths" in vision_understanding.tool_call_schema.model_json_schema()[
        "properties"
    ]


def test_video_content_marks_image_video_and_audio_roles(tmp_path: Path):
    image = tmp_path / "avatar.png"
    video = tmp_path / "prev.mp4"
    audio = tmp_path / "line.mp3"
    image.write_bytes(b"png")
    video.write_bytes(b"mp4")
    audio.write_bytes(b"mp3")

    items = _content(
        "参考[图1]的人物",
        [str(image), str(video)],
        [str(audio)],
        "ref2va",
    )
    assert items[0] == {"type": "text", "text": "参考[图1]的人物"}
    assert items[1]["role"] == "reference_image"
    assert items[2]["role"] == "reference_video"
    assert items[3]["role"] == "reference_audio"
    assert _duration_seconds(3.2) == 4
    assert _duration_seconds(8.4) == 8
    assert _duration_seconds(20) == 15

    first_frame = _content("从这张图开始动", [str(image)], None, "fl2va")
    assert first_frame[1]["role"] == "first_frame"
    with pytest.raises(ValueError, match="不能传参考音频"):
        _content("从这张图开始动", [str(image)], [str(audio)], "fl2va")
    with pytest.raises(ValueError, match="只能传入一张首帧图"):
        _content("从这张图开始动", [str(image), str(image)], None, "fl2va")


def test_image_gen_writes_project_file(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "ugc_agent.tools.image_gen.generate_image",
        lambda prompt, *, reference_paths=None, size="2K": b"png-bytes",
    )
    store = ProjectStore(tmp_path)
    store.create("demo")
    result = invoke_turn(
        build_agent(
            ToolCallingFakeModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "image-gen",
                                "id": "img-1",
                                "args": {"prompt": "白底人物锚图"},
                            }
                        ],
                    ),
                    AIMessage(content="图已生成。"),
                ]
            )
        ),
        "先出人物锚图",
        thread_id="thread-1",
        context=AgentContext("demo", "turn-1", store),
    )
    payload = _tool_payload(result, "image-gen")
    path = Path(payload["path"])
    assert payload["kind"] == "image"
    assert path.read_bytes() == b"png-bytes"
    assert path.parent == tmp_path / "demo" / "media" / "images"
    assert store.load("demo").media == {}


def test_video_gen_writes_project_file(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "ugc_agent.tools.video_gen.generate_video",
        lambda prompt, **kwargs: b"mp4-bytes",
    )
    store = ProjectStore(tmp_path)
    store.create("demo")
    agent = build_agent(
        ToolCallingFakeModel(
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
    )
    paused = invoke_turn(
        agent,
        "生成第一段",
        thread_id="thread-1",
        context=AgentContext("demo", "turn-1", store),
    )
    interrupt = paused["__interrupt__"][0]
    result = resume_turn(
        agent,
        {interrupt.id: {"type": "approve"}},
        thread_id="thread-1",
        context=AgentContext("demo", "turn-2", store),
    )
    payload = _tool_payload(result, "video-gen")
    path = Path(payload["path"])
    assert payload["kind"] == "video"
    assert path.read_bytes() == b"mp4-bytes"
    assert path.parent == tmp_path / "demo" / "media" / "videos"


def test_vision_understanding_returns_model_text(monkeypatch):
    monkeypatch.setattr(
        "ugc_agent.tools.vision_understanding.describe_media",
        lambda prompt, media_paths=None: "人物穿白衬衫，面对镜头说话",
    )
    result = vision_understanding.invoke({"prompt": "描述这个人"})
    assert result == "人物穿白衬衫，面对镜头说话"


def test_system_prompt_picks_minimax_tool_and_skill(monkeypatch):
    monkeypatch.setenv("UGC_VIDEO_PROVIDER", "minimax")
    prompt = system_prompt()
    assert "video-gen-minimax" in prompt
    assert "h3-prompt" in prompt
    assert "不要调用 video-gen" in prompt
    monkeypatch.setenv("UGC_VIDEO_PROVIDER", "seedance")
    seedance = system_prompt()
    assert "load_skill seedance-prompt" in seedance
    assert "不要调用 video-gen-minimax" in seedance


def test_minimax_create_form_uses_prompt_seconds_size(tmp_path):
    image = tmp_path / "frame.png"
    image.write_bytes(b"png")
    fields, files = _form_parts(
        "图1作为首帧",
        duration_seconds=5,
        mode="fl2va",
        reference_paths=[str(image)],
        audio_paths=None,
        aspect_ratio="9:16",
        resolution="720p",
    )
    names = [name for name, _ in fields]
    assert ("model", "/models/MiniMax-H3") in fields
    assert ("prompt", "图1作为首帧") in fields
    assert ("seconds", "5") in fields
    assert ("size", "768x1344") in fields
    assert "content" not in names
    assert "duration" not in names
    assert "resolution" not in names
    assert "ratio" not in names
    assert files[0][0] == "input_reference"
    assert files[0][1] == "frame.png"
    body, content_type = _encode_multipart(fields, files)
    assert content_type.startswith("multipart/form-data")
    text = body.decode("utf-8", errors="replace")
    assert 'name="prompt"' in text
    assert 'name="seconds"' in text
    assert 'name="size"' in text
    assert 'name="input_reference"' in text
    assert 'name="content"' not in text
    assert 'name="duration"' not in text
    assert 'name="resolution"' not in text
    assert 'name="ratio"' not in text


def test_minimax_http_paths_use_reference_url():
    fields, files = _form_parts(
        "参考图1",
        duration_seconds=6,
        mode="ref2va",
        reference_paths=["https://cdn.example/a.png"],
        audio_paths=None,
        aspect_ratio="9:16",
        resolution="768P",
    )
    assert ("reference_url", "https://cdn.example/a.png") in fields
    assert files == []


def test_minimax_create_posts_multipart_then_polls(tmp_path, monkeypatch):
    image = tmp_path / "frame.png"
    image.write_bytes(b"png")
    monkeypatch.setenv("MINIMAX_API_KEY", "mm-key")
    captured = {}

    def fake_post(url, fields, files, *, timeout=60):
        captured["create_url"] = url
        captured["fields"] = fields
        captured["files"] = files
        captured["content_type"] = "multipart/form-data"
        return {"id": "4240"}

    def fake_get(url, *, api_key, payload=None, method="POST", timeout=60):
        captured["query_url"] = url
        captured["query_method"] = method
        assert method == "GET"
        return {"status": "completed", "url": "https://cdn.example/out.mp4"}

    monkeypatch.setattr("ugc_agent.tools.video_gen_minimax.client.post_form", fake_post)
    monkeypatch.setattr("ugc_agent.tools.video_gen_minimax.client.request_json", fake_get)
    monkeypatch.setattr(
        "ugc_agent.tools.video_gen_minimax.client.download_bytes",
        lambda url: b"mp4-bytes",
    )
    data = generate_minimax_video(
        "图1作为首帧，人物向前走",
        duration_seconds=5,
        mode="fl2va",
        reference_paths=[str(image)],
        sleep=lambda _: None,
    )
    assert data == b"mp4-bytes"
    assert captured["create_url"] == "https://api.minimax.cn/v1/videos"
    assert captured["content_type"].startswith("multipart/form-data")
    assert ("prompt", "图1作为首帧，人物向前走") in captured["fields"]
    assert ("seconds", "5") in captured["fields"]
    assert ("size", "768x1344") in captured["fields"]
    assert captured["files"][0][0] == "input_reference"
    assert captured["query_url"] == "https://api.minimax.cn/v1/videos/4240"
    assert captured["query_method"] == "GET"
