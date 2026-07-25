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

    def generate(self, prompt: str, output_type: type[T]) -> T:
        active_prompt = prompt
        last_text = ""
        last_error = ""
        for attempt in range(2):
            response = self.client.chat.completions.create(
                model=self.settings.model,
                temperature=0.55 if attempt == 0 else 0.2,
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
                active_prompt = repair_prompt(prompt, last_text, last_error)
        raise RuntimeError(
            f"Model output failed validation after repair: {last_error}\n"
            f"Last response preview: {last_text[:500]}"
        )
