from __future__ import annotations

import json
import re
from typing import TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from .llm_prompts import SYSTEM_PROMPT, repair_prompt
from .settings import LLMSettings

T = TypeVar("T", bound=BaseModel)


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

    def generate(
        self,
        prompt: str,
        output_type: type[T],
        *,
        system: str | None = None,
    ) -> T:
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
                    {"role": "system", "content": system or SYSTEM_PROMPT},
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
