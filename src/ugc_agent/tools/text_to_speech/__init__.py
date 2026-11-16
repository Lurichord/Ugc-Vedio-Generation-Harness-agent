from __future__ import annotations

from langchain.tools import ToolRuntime, tool
from pydantic import BaseModel, Field

from ...context import AgentContext
from .client import resolve_voice_id, synthesize_speech, write_speech_files

__all__ = ["text_to_speech"]


class TextToSpeechInput(BaseModel):
    text: str = Field(
        description="这一段要朗读的口播原文。一次只传一个镜头的台词，不要把全片拼进同一次调用。"
    )
    voice_id: str | None = Field(
        default=None,
        description="音色 ID。全片必须用同一个值。不传则使用环境变量 VOLCENGINE_TTS_VOICE_ID。",
    )
    speed: int = Field(
        default=0,
        ge=-50,
        le=100,
        description="语速，范围 -50 到 100，0 为默认。100 是 2 倍速，-50 是 0.5 倍速。全片保持不变。",
    )
    pitch: int = Field(
        default=0,
        ge=-12,
        le=12,
        description="音高，范围 -12 到 12，0 为默认。全片保持不变。",
    )
    volume: int = Field(
        default=0,
        ge=-50,
        le=100,
        description="音量，范围 -50 到 100，0 为默认。100 是 2 倍音量，-50 是 0.5 倍。全片保持不变。",
    )


@tool("text-to-speech", args_schema=TextToSpeechInput)
def text_to_speech(
    text: str,
    runtime: ToolRuntime[AgentContext],
    voice_id: str | None = None,
    speed: int = 0,
    pitch: int = 0,
    volume: int = 0,
) -> dict:
    """按镜头把一段口播文案合成 mp3，并写出字级时间戳文件。

使用时机：
- 数字人讲解、广告、科普：生成视频前必须按镜头各调用一次
- 同一条片子里 voice_id、speed、pitch、volume 必须保持一致
- 演绎短剧不要调用；对白写进视频 prompt，音频连续用 split-audio

不要做：
- 不要一次传入全片文案
- 不要手写或估算字幕时间；字幕只能用返回的 timestamp_path
- 不要把结果写入项目状态；拿到路径后自己 record_media

返回：
- path：mp3 文件路径
- timestamp_path：字级时间戳 json 路径，字段为 text 和 words[].word/start/end（秒）
- duration_seconds：按时间戳末字结束时间计算
- voice_id、speed、pitch、volume：这次实际使用的参数，后续镜头应原样回传
"""
    stripped = text.strip()
    if not stripped:
        raise ValueError("text is empty")
    resolved_voice = resolve_voice_id(voice_id)
    audio, words = synthesize_speech(
        stripped,
        voice_id=resolved_voice,
        speed=speed,
        pitch=pitch,
        volume=volume,
    )
    directory = (
        runtime.context.project_store.root
        / runtime.context.project_id
        / "media"
        / "audio"
    )
    audio_path, timestamp_path, duration = write_speech_files(
        directory, stripped, audio, words
    )
    return {
        "kind": "audio",
        "path": str(audio_path),
        "timestamp_path": str(timestamp_path),
        "duration_seconds": duration,
        "voice_id": resolved_voice,
        "speed": speed,
        "pitch": pitch,
        "volume": volume,
    }
