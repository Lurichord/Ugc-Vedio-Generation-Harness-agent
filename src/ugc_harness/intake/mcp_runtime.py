"""Talk to ugc-intake over MCP. skill.activate stays on the host."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from ..agents.generic import McpToolTransport
from ..tools.mcp import intake_stdio_server_config
from .models import IntakeSession
from .tools import (
    CONFIGURE_SESSION,
    INTAKE_MCP_TOOLS,
    SKILL_ACTIVATE,
    activate_skill,
    host_tool_specs,
    mcp_tool_specs,
)


class IntakeMcpRuntime:
    """One beat: write session, open ugc-intake, optional tool calls, reload session."""

    def __init__(
        self,
        session_path: Path,
        *,
        dry_run: bool = False,
        output_root: Path | str | None = None,
    ) -> None:
        self.session_path = Path(session_path)
        self.dry_run = dry_run
        self.output_root = Path(output_root) if output_root else None
        self._channel: Any | None = None
        self.session: IntakeSession | None = None

    def write_session(self, session: IntakeSession) -> None:
        self.session = session
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_path.write_text(
            session.model_dump_json(indent=2),
            encoding="utf-8",
        )

    def read_session(self) -> IntakeSession:
        session = IntakeSession.model_validate_json(
            self.session_path.read_text(encoding="utf-8")
        )
        self.session = session
        return session

    def specs(self) -> list[dict[str, Any]]:
        return [*mcp_tool_specs(), *host_tool_specs()]

    def configure_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "session_path": str(self.session_path),
            "dry_run": self.dry_run,
        }
        if self.output_root is not None:
            payload["output_root"] = str(self.output_root)
        return payload

    @asynccontextmanager
    async def connect(self, session: IntakeSession) -> AsyncIterator["IntakeMcpRuntime"]:
        self.write_session(session)
        transport = McpToolTransport(
            intake_stdio_server_config(),
            configure_tool=CONFIGURE_SESSION,
            configure_payload=self.configure_payload(),
        )
        async with transport.open() as channel:
            self._channel = channel
            try:
                yield self
            finally:
                self._channel = None

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        args = arguments or {}
        if name == SKILL_ACTIVATE:
            return activate_skill(str(args.get("name") or ""))
        if name not in INTAKE_MCP_TOOLS:
            return {"ok": False, "error": f"未知工具：{name}"}
        if self._channel is None:
            return {"ok": False, "error": "ugc-intake MCP 未连接"}
        result = await self._call_channel(name, args)
        if self.session_path.is_file():
            self.read_session()
        return result

    async def _call_channel(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        call = self._channel.call
        result = call(name, args)
        if asyncio.iscoroutine(result):
            result = await result
        if not isinstance(result, dict):
            return {"ok": False, "error": f"{name} 返回了非对象结果"}
        return result
