"""Live CLI audience rounds. Default skip; the driver talks to real ugc-intake."""

from __future__ import annotations

import os

import pytest

from tests.run_intent_cli_audience import PERSONAS, main as run_audience


@pytest.mark.skipif(
    os.environ.get("UGC_LIVE_INTAKE") != "1",
    reason="真实 CLI 对话要烧 LLM。需要时设 UGC_LIVE_INTAKE=1",
)
def test_live_cli_audience_rounds() -> None:
    assert len(PERSONAS) == 10
    assert run_audience(["--fresh"]) == 0
