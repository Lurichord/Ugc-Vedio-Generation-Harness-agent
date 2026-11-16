from __future__ import annotations

from typing import Any


def interrupt_payload(result: dict | None) -> dict | None:
    items = (result or {}).get("__interrupt__") or ()
    if not items:
        return None
    item = items[0]
    value = item.value
    return {
        "id": item.id,
        "name": value["name"],
        "args": value["args"],
        "description": value.get("description") or "",
    }


def snapshot_interrupt(snapshot: Any) -> dict | None:
    items = getattr(snapshot, "interrupts", None) or ()
    if not items:
        return None
    return interrupt_payload({"__interrupt__": items})


def last_assistant_text(messages: list[Any]) -> str | None:
    for message in reversed(messages):
        if _is_ai(message) and not _tool_calls(message):
            return _text(message)
    return None


def project_chat(messages: list[Any]) -> list[dict]:
    chat: list[dict] = []
    for message in messages:
        if _is_human(message):
            chat.append({"role": "user", "content": _text(message)})
        elif _is_ai(message) and not _tool_calls(message):
            chat.append({"role": "assistant", "content": _text(message)})
    return chat


def project_run(messages: list[Any]) -> list[dict]:
    turns: list[dict] = []
    current: dict | None = None
    for message in messages:
        if _is_human(message):
            current = {
                "turn_id": _turn_id(message) or f"turn_{len(turns) + 1}",
                "user": _text(message),
                "steps": [],
            }
            turns.append(current)
            continue
        if current is None:
            continue
        if _is_ai(message):
            current["steps"].append(
                {
                    "index": len(current["steps"]) + 1,
                    "assistant": _text(message),
                    "tool_calls": _tool_calls(message),
                    "tool_results": [],
                }
            )
            continue
        if _is_tool(message) and current["steps"]:
            current["steps"][-1]["tool_results"].append(
                {
                    "name": getattr(message, "name", "") or "",
                    "ok": getattr(message, "status", "success") != "error",
                }
            )
    return turns


def _is_human(message: Any) -> bool:
    return getattr(message, "type", None) == "human"


def _is_ai(message: Any) -> bool:
    return getattr(message, "type", None) == "ai"


def _is_tool(message: Any) -> bool:
    return getattr(message, "type", None) == "tool"


def _text(message: Any) -> str:
    content = getattr(message, "content", "")
    return content if isinstance(content, str) else ""


def _turn_id(message: Any) -> str | None:
    kwargs = getattr(message, "additional_kwargs", None) or {}
    value = kwargs.get("turn_id")
    return value if isinstance(value, str) else None


def _tool_calls(message: Any) -> list[dict]:
    calls = getattr(message, "tool_calls", None) or []
    projected = []
    for call in calls:
        if isinstance(call, dict):
            projected.append(
                {"name": call.get("name", ""), "args": call.get("args", {})}
            )
        else:
            projected.append(
                {
                    "name": getattr(call, "name", ""),
                    "args": getattr(call, "args", {}),
                }
            )
    return projected
