"""In-process stand-in for ugc-intake stdio. Tests only."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator
from uuid import uuid4

from ugc_harness.intake.mcp_runtime import IntakeMcpRuntime
from ugc_harness.intake.models import IntakeSession
from ugc_harness.intake.tools import (
    DESCRIPTION_GET_ELEMENT,
    DESCRIPTION_LIST_OUTLINE,
    HARNESS_INSPECT,
    HARNESS_LIST_GRAPH,
    HARNESS_REPAIR,
    HARNESS_START_PROJECT,
    INTAKE_MCP_TOOLS,
)
from ugc_harness.mcp_servers import intake as intake_mcp


class DirectIntakeMcpChannel:
    """Same tool functions as the stdio server, without a subprocess."""

    def attach(self, payload: dict[str, Any]) -> None:
        intake_mcp.reset()
        intake_mcp.configure_session(
            payload["session_path"],
            dry_run=bool(payload.get("dry_run", False)),
            output_root=payload.get("output_root"),
        )

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        args = arguments or {}
        if name not in INTAKE_MCP_TOOLS:
            return {"ok": False, "error": f"未知 MCP 工具：{name}"}
        if name == DESCRIPTION_LIST_OUTLINE:
            return intake_mcp.description_list_outline()
        if name == DESCRIPTION_GET_ELEMENT:
            return intake_mcp.description_get_element(str(args.get("ref") or ""))
        if name == HARNESS_INSPECT:
            return intake_mcp.harness_inspect()
        if name == HARNESS_LIST_GRAPH:
            around = args.get("around_ref")
            return intake_mcp.harness_list_graph(
                around_ref=str(around).strip() if around else None
            )
        if name == HARNESS_START_PROJECT:
            return intake_mcp.harness_start_project()
        if name == HARNESS_REPAIR:
            refs = args.get("target_refs") or []
            if isinstance(refs, str):
                refs = [refs]
            return intake_mcp.harness_repair(
                list(refs),
                str(args.get("instruction") or ""),
            )
        return {"ok": False, "error": f"未知 MCP 工具：{name}"}


class RecordingIntakeRuntime(IntakeMcpRuntime):
    """Host-loop tests: same tools as production, no stdio spawn."""

    def __init__(
        self,
        session_path: Path,
        *,
        dry_run: bool = False,
        output_root: Path | str | None = None,
    ) -> None:
        super().__init__(session_path, dry_run=dry_run, output_root=output_root)
        self.mcp_calls: list[tuple[str, dict[str, Any]]] = []
        self._direct = DirectIntakeMcpChannel()

    @asynccontextmanager
    async def connect(self, session: IntakeSession) -> AsyncIterator["RecordingIntakeRuntime"]:
        self.mcp_calls = []
        self.write_session(session)
        self._direct.attach(self.configure_payload())
        self._channel = self._direct
        try:
            yield self
        finally:
            self._channel = None

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        args = arguments or {}
        if name in INTAKE_MCP_TOOLS:
            self.mcp_calls.append((name, args))
        return await super().call(name, arguments)


def make_recording_runtime(*, dry_run: bool = True) -> RecordingIntakeRuntime:
    path = Path(".tmp") / "intake_mcp" / f"{uuid4().hex}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return RecordingIntakeRuntime(path, dry_run=dry_run)
