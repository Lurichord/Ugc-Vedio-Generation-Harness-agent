from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from .llm_prompts import SYSTEM_PROMPT, repair_prompt
from .settings import LLMSettings

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class ModelToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]

    def assistant_message(self) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": self.call_id,
                    "type": "function",
                    "function": {
                        "name": self.name,
                        "arguments": json.dumps(
                            self.arguments,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    },
                }
            ],
        }


def _extract_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("model response does not contain a JSON object")
        value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("model response must be a JSON object")
    return value


class StructuredLLM:
    def __init__(self, settings: LLMSettings):
        self.settings = settings
        self.client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=settings.timeout_seconds,
            max_retries=settings.max_retries,
        )

    def generate(self, prompt: str, output_type: type[T]) -> T:
        schema = json.dumps(
            output_type.model_json_schema(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        schema_prompt = (
            f"{prompt}\n\n"
            f"输出必须严格符合 {output_type.__name__} 的 JSON Schema。"
            "字段名、类型、必填项和枚举都必须完全一致，不得添加 Schema 外字段。\n"
            f"JSON Schema:\n{schema}"
        )
        active_prompt = schema_prompt
        last_text = ""
        last_error = ""
        for attempt in range(2):
            response = self.client.chat.completions.create(
                model=self.settings.model,
                temperature=0.55 if attempt == 0 else 0.2,
                max_tokens=self.settings.max_output_tokens,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": active_prompt},
                ],
            )
            last_text = response.choices[0].message.content or ""
            try:
                return output_type.model_validate(_extract_json(last_text))
            except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                last_error = str(exc)
                active_prompt = repair_prompt(
                    schema_prompt,
                    last_text,
                    last_error,
                )
        raise RuntimeError(
            f"Model output failed validation after repair: {last_error}\n"
            f"Last response preview: {last_text[:500]}"
        )

    def choose_tool(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ModelToolCall:
        """Ask the model to select exactly one tool for the next agent step."""

        if not tools:
            raise ValueError("at least one model tool is required")
        active_messages = list(messages)
        calls = []
        arguments: dict[str, Any] | None = None
        argument_error: Exception | None = None
        for attempt in range(2):
            response = self.client.chat.completions.create(
                model=self.settings.model,
                temperature=0.1,
                max_tokens=min(self.settings.max_output_tokens, 2_048),
                messages=active_messages,  # type: ignore[arg-type]
                tools=tools,  # type: ignore[arg-type]
                tool_choice="required",
            )
            calls = response.choices[0].message.tool_calls or []
            if len(calls) == 1:
                try:
                    arguments = _extract_json(calls[0].function.arguments or "{}")
                    break
                except (ValueError, json.JSONDecodeError) as exc:
                    argument_error = exc
            if attempt == 0:
                active_messages = [
                    *messages,
                    {
                        "role": "user",
                        "content": (
                            "你必须从当前提供的工具中选择且只调用一个工具。"
                            "不要只返回文字；function.arguments 必须是严格 JSON 对象。"
                            "请根据现有工具结果决定下一步。"
                        ),
                    },
                ]
        if len(calls) != 1 or arguments is None:
            if argument_error is not None:
                raise RuntimeError(
                    "agent model returned invalid tool arguments after retry"
                ) from argument_error
            raise RuntimeError(
                f"agent model must choose exactly one tool, received {len(calls)}"
            )
        call = calls[0]
        return ModelToolCall(
            call_id=call.id,
            name=call.function.name,
            arguments=arguments,
        )
