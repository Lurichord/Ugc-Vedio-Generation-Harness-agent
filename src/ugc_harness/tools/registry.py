from __future__ import annotations

from collections.abc import Callable
from typing import Any


class ToolNotAllowedError(PermissionError):
    pass


class ToolRegistry:
    """Small local MCP-shaped boundary: agents call names, never providers directly."""

    def __init__(self) -> None:
        self._tools: dict[str, Callable[..., Any]] = {}

    def register(self, name: str, tool: Callable[..., Any]) -> None:
        if not name or name in self._tools:
            raise ValueError(f"tool is already registered or invalid: {name!r}")
        self._tools[name] = tool

    def invoke(
        self,
        name: str,
        *,
        allowed_tools: list[str],
        **kwargs: Any,
    ) -> Any:
        if name not in allowed_tools:
            raise ToolNotAllowedError(f"tool is not allowed by task envelope: {name}")
        try:
            tool = self._tools[name]
        except KeyError as exc:
            raise KeyError(f"tool is not registered: {name}") from exc
        return tool(**kwargs)

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self._tools)
