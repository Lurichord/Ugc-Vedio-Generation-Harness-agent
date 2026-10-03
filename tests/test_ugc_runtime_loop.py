from __future__ import annotations

import json

from ugc_runtime.loop import Step, run_loop


def _scripted(*steps: Step):
    pending = list(steps)

    def decide(messages, tool_names):
        del messages, tool_names
        return pending.pop(0)

    return decide


def _loads(message: dict) -> dict:
    return json.loads(message["content"])


def test_echo_then_stop():
    seen: list[dict] = []

    def echo(arguments: dict) -> dict:
        seen.append(arguments)
        return {"text": arguments["text"]}

    result = run_loop(
        [{"role": "user", "content": "hi"}],
        decide=_scripted(
            Step(tool="echo", arguments={"text": "hi"}),
            Step(message="好了"),
        ),
        tools={"echo": echo},
    )

    assert result.stop == "done"
    assert seen == [{"text": "hi"}]
    assert [item.tool for item in result.steps] == ["echo", None]
    assert _loads(result.messages[-2])["result"] == {"text": "hi"}
    assert _loads(result.messages[-1])["message"] == "好了"
    assert result.messages[0] == {"role": "user", "content": "hi"}


def test_unknown_tool_continues():
    result = run_loop(
        [],
        decide=_scripted(
            Step(tool="missing"),
            Step(message="停"),
        ),
        tools={},
    )

    assert result.stop == "done"
    body = _loads(result.messages[1])
    assert body["result"]["ok"] is False
    assert "unknown_tool" in body["result"]["error"]


def test_tool_error_continues():
    def broken(arguments: dict) -> dict:
        del arguments
        raise RuntimeError("boom")

    result = run_loop(
        [],
        decide=_scripted(
            Step(tool="broken"),
            Step(message="停"),
        ),
        tools={"broken": broken},
    )

    assert result.stop == "done"
    assert "boom" in _loads(result.messages[1])["result"]["error"]


def test_budget():
    calls = 0

    def echo(arguments: dict) -> dict:
        del arguments
        nonlocal calls
        calls += 1
        return {"ok": True}

    def decide(messages, tool_names):
        del messages, tool_names
        return Step(tool="echo")

    result = run_loop([], decide=decide, tools={"echo": echo}, max_steps=1)

    assert result.stop == "budget"
    assert calls == 1
    assert len(result.steps) == 1
