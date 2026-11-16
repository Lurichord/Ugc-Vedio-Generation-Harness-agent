import base64
import json
from pathlib import Path

import pytest
from langchain.messages import AIMessage, ToolMessage
from langchain_core.language_models.fake_chat_models import (
    FakeMessagesListChatModel,
)

from ugc_agent.agent import build_agent, invoke_turn
from ugc_agent.context import AgentContext
from ugc_agent.store import ProjectStore
from ugc_agent.tools.text_to_speech import text_to_speech
from ugc_agent.tools.text_to_speech.client import parse_tts_stream, write_speech_files


class ToolCallingFakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def _chunk(text: str, words: list[dict], audio: bytes) -> str:
    return json.dumps(
        {
            "code": 0,
            "message": "OK",
            "data": base64.b64encode(audio).decode("ascii"),
            "sentence": {"text": text, "words": words, "phonemes": []},
        }
    )


def test_text_to_speech_tool_describes_usage_and_parameters():
    description = text_to_speech.description
    assert "数字人讲解、广告、科普" in description
    assert "演绎短剧不要调用" in description
    assert "timestamp_path" in description
    schema = text_to_speech.tool_call_schema.model_json_schema()
    properties = schema["properties"]
    assert "text" in properties
    assert "voice_id" in properties
    assert "speed" in properties
    assert "这一段要朗读的口播原文" in properties["text"]["description"]
    assert "音色 ID" in properties["voice_id"]["description"]
    assert "runtime" not in properties


def test_parse_tts_stream_collects_audio_and_unique_words():
    words = [
        {"word": "你", "startTime": 0.1, "endTime": 0.3, "confidence": 0.9},
        {"word": "好", "startTime": 0.3, "endTime": 0.6, "confidence": 0.9},
    ]
    audio, parsed = parse_tts_stream(
        [
            _chunk("你好", words, b"abc"),
            _chunk("你好", words, b"def"),
            json.dumps({"code": 20000000, "message": "done"}),
        ]
    )

    assert audio == b"abcdef"
    assert parsed == [
        {"word": "你", "start": 0.1, "end": 0.3},
        {"word": "好", "start": 0.3, "end": 0.6},
    ]


def test_parse_tts_stream_raises_on_error_code():
    with pytest.raises(RuntimeError, match="quota"):
        parse_tts_stream([json.dumps({"code": 45000000, "message": "quota"})])


def test_write_speech_files_persists_audio_and_timestamp(tmp_path: Path):
    audio_path, timestamp_path, duration = write_speech_files(
        tmp_path / "audio",
        "你好",
        b"ID3fake",
        [{"word": "你", "start": 0.1, "end": 0.3}, {"word": "好", "start": 0.3, "end": 0.8}],
    )

    assert audio_path.read_bytes() == b"ID3fake"
    assert timestamp_path.name.endswith(".timestamp.json")
    payload = json.loads(timestamp_path.read_text(encoding="utf-8"))
    assert payload["text"] == "你好"
    assert payload["words"][1]["word"] == "好"
    assert duration == 0.8


def test_text_to_speech_writes_project_files_and_returns_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("VOLCENGINE_TTS_VOICE_ID", "zh_male_test")
    monkeypatch.setenv("VOLCENGINE_TTS_API_KEY", "test-key")

    def fake_synthesize(text, *, voice_id, speed, pitch, volume):
        assert text == "这一段口播"
        assert voice_id == "zh_male_test"
        assert (speed, pitch, volume) == (0, 0, 0)
        return b"mp3-bytes", [{"word": "这", "start": 0.12, "end": 0.4}]

    monkeypatch.setattr(
        "ugc_agent.tools.text_to_speech.client.synthesize_speech", fake_synthesize
    )
    monkeypatch.setattr(
        "ugc_agent.tools.text_to_speech.synthesize_speech", fake_synthesize
    )
    store = ProjectStore(tmp_path)
    store.create("demo")
    model = ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "text-to-speech",
                        "id": "tts-1",
                        "args": {"text": "这一段口播"},
                    }
                ],
            ),
            AIMessage(content="口播已生成。"),
        ]
    )
    result = invoke_turn(
        build_agent(model),
        "先配这一段口播",
        thread_id="thread-1",
        context=AgentContext("demo", "turn-1", store),
    )
    tool_messages = [
        message
        for message in result["messages"]
        if isinstance(message, ToolMessage) and message.name == "text-to-speech"
    ]
    assert len(tool_messages) == 1
    payload = json.loads(tool_messages[0].content)
    audio_path = Path(payload["path"])
    timestamp_path = Path(payload["timestamp_path"])

    assert payload["kind"] == "audio"
    assert payload["voice_id"] == "zh_male_test"
    assert payload["duration_seconds"] == 0.4
    assert audio_path.read_bytes() == b"mp3-bytes"
    assert timestamp_path.is_file()
    assert audio_path.parent == tmp_path / "demo" / "media" / "audio"
    saved = json.loads(timestamp_path.read_text(encoding="utf-8"))
    assert saved["text"] == "这一段口播"
    assert store.load("demo").media == {}
