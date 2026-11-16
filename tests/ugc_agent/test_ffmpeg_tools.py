import json
from pathlib import Path

from langchain.messages import AIMessage, ToolMessage
from langchain_core.language_models.fake_chat_models import (
    FakeMessagesListChatModel,
)

from ugc_agent.agent import build_agent, invoke_turn
from ugc_agent.context import AgentContext
from ugc_agent.store import ProjectStore
from ugc_agent.tools.extract_video_frame import extract_video_frame
from ugc_agent.tools.ffmpeg import _parse_seconds, frame_times
from ugc_agent.tools.merge_video import merge_video
from ugc_agent.tools.split_audio import split_audio


class ToolCallingFakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def _payload(result: dict, name: str) -> dict:
    messages = [
        message
        for message in result["messages"]
        if isinstance(message, ToolMessage) and message.name == name
    ]
    assert len(messages) == 1
    return json.loads(messages[0].content)


def test_parse_seconds_skips_missing_stream_duration():
    assert _parse_seconds("12.041667\n") == 12.041667
    assert _parse_seconds("N/A") is None
    assert _parse_seconds("") is None


def test_frame_times_end_even_and_seconds():
    assert frame_times(10, 1, "end") == [9.95]
    assert frame_times(10, 1, "start") == [0.0]
    assert frame_times(10, 3, "even") == [0.0, 5.0, 9.95]
    assert frame_times(10, 1, "1.2") == [1.2]


def test_ffmpeg_tools_describe_usage():
    assert "尾帧" in extract_video_frame.description
    assert "count" in extract_video_frame.tool_call_schema.model_json_schema()["properties"]
    assert "演绎短剧" in split_audio.description
    assert "按镜头顺序" in merge_video.description


def test_extract_split_merge_write_project_files(tmp_path, monkeypatch):
    def fake_extract(video_path, directory, *, count=1, at=None):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "frame.png"
        path.write_bytes(b"png")
        return [path]

    def fake_split(video_path, directory):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "track.mp3"
        path.write_bytes(b"mp3")
        return path

    def fake_merge(video_paths, directory):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "final.mp4"
        path.write_bytes(b"mp4")
        return path

    monkeypatch.setattr(
        "ugc_agent.tools.extract_video_frame.extract_frames", fake_extract
    )
    monkeypatch.setattr("ugc_agent.tools.split_audio.split_audio_file", fake_split)
    monkeypatch.setattr("ugc_agent.tools.merge_video.merge_videos", fake_merge)
    store = ProjectStore(tmp_path)
    store.create("demo")
    model = ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "extract-video-frame",
                        "id": "f1",
                        "args": {"video_path": "seg.mp4"},
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "split-audio",
                        "id": "a1",
                        "args": {"video_path": "seg.mp4"},
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "merge-video",
                        "id": "m1",
                        "args": {"video_paths": ["a.mp4", "b.mp4"]},
                    }
                ],
            ),
            AIMessage(content="完成。"),
        ]
    )
    result = invoke_turn(
        build_agent(model),
        "抽帧拆音频再拼接",
        thread_id="thread-1",
        context=AgentContext("demo", "turn-1", store),
    )
    frame = _payload(result, "extract-video-frame")
    audio = _payload(result, "split-audio")
    merged = _payload(result, "merge-video")
    assert Path(frame["path"]).read_bytes() == b"png"
    assert frame["paths"] == [frame["path"]]
    assert Path(audio["path"]).read_bytes() == b"mp3"
    assert Path(merged["path"]).read_bytes() == b"mp4"
    assert store.load("demo").media == {}
