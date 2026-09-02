"""Shared skip/dump helpers for live LLM component tests."""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from ugc_harness.shared.settings import LLMSettings


def live_settings() -> LLMSettings:
    if os.environ.get("UGC_LIVE") != "1":
        pytest.skip("set UGC_LIVE=1 to run live model tests")
    try:
        return LLMSettings.from_environment(None, None)
    except ValueError as exc:
        pytest.skip(str(exc))


def dump_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)
