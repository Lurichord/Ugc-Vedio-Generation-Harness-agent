from __future__ import annotations

import os
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, TextIO

from mcp import StdioServerParameters


def mcp_tool_catalog(
    server: Any,
    *,
    names: tuple[str, ...] | None = None,
    exclude: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Read name / description / parameters from an in-process MCPServer."""

    manager = getattr(server, "_tool_manager", None)
    if manager is None or not hasattr(manager, "list_tools"):
        raise TypeError("MCP server has no tool manager")
    listed = {tool.name: tool for tool in manager.list_tools()}
    order = names if names is not None else tuple(listed)
    catalog: list[dict[str, Any]] = []
    for name in order:
        if name in exclude:
            continue
        tool = listed.get(name)
        if tool is None:
            raise RuntimeError(f"MCP server is missing tool: {name}")
        parameters = getattr(tool, "parameters", None)
        if not isinstance(parameters, dict):
            parameters = getattr(tool, "input_schema", None)
        if not isinstance(parameters, dict):
            parameters = {"type": "object", "properties": {}}
        catalog.append(
            {
                "name": name,
                "description": str(getattr(tool, "description", None) or "").strip(),
                "parameters": parameters,
            }
        )
    return catalog


@dataclass(frozen=True)
class StdioMCPServerConfig:
    """Configuration for an MCP server spawned as a stdio child process."""

    command: str
    args: tuple[str, ...]
    cwd: Path
    env: dict[str, str] = field(default_factory=dict)

    def server_parameters(self) -> StdioServerParameters:
        return StdioServerParameters(
            command=self.command,
            args=list(self.args),
            cwd=self.cwd,
            env=self.env or None,
            encoding="utf-8",
            encoding_error_handler="strict",
        )

    def with_environment(self, values: dict[str, str]) -> "StdioMCPServerConfig":
        return StdioMCPServerConfig(
            command=self.command,
            args=self.args,
            cwd=self.cwd,
            env={**self.env, **values},
        )


def _stdio_server_config(module: str) -> StdioMCPServerConfig:
    source_root = Path(__file__).resolve().parents[2]
    project_root = source_root.parent
    env = dict(os.environ)
    current_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{source_root}{os.pathsep}{current_pythonpath}"
        if current_pythonpath
        else str(source_root)
    )
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault(
        "AGENT_TOOL_LOG",
        str(project_root / "app" / "data" / "logs" / "agent-tools.log"),
    )
    return StdioMCPServerConfig(
        command=sys.executable,
        args=("-m", module),
        cwd=project_root,
        env=env,
    )


def narrative_stdio_server_config() -> StdioMCPServerConfig:
    """Launch the bundled Narrative MCP server with the active interpreter."""

    return _stdio_server_config("ugc_harness.mcp_servers.narrative")


def intake_stdio_server_config() -> StdioMCPServerConfig:
    """Launch the bundled intent-layer MCP server. Isolated from narrative.*."""

    return _stdio_server_config("ugc_harness.mcp_servers.intake")


@contextmanager
def forward_child_stderr(dest: TextIO | None = None) -> Iterator[TextIO]:
    """Pipe a child's stderr back onto this process's terminal.

    Windows MCP spawn uses CREATE_NO_WINDOW. The child then has no console, so
    inheriting sys.stderr often drops prints even when the parent can see
    ``[intake]`` from the same terminal. A pipe plus a pump thread does not
    depend on the child being attached to a console.
    """

    output = dest or sys.stderr
    read_fd, write_fd = os.pipe()
    try:
        os.set_inheritable(write_fd, True)
    except OSError:
        pass
    reader = os.fdopen(
        read_fd, "r", encoding="utf-8", errors="replace", buffering=1, closefd=True
    )
    errlog = os.fdopen(
        write_fd, "w", encoding="utf-8", errors="replace", buffering=1, closefd=True
    )

    def _pump() -> None:
        try:
            for line in reader:
                output.write(line)
                output.flush()
        except OSError:
            return

    worker = threading.Thread(target=_pump, name="mcp-stderr", daemon=True)
    worker.start()
    try:
        yield errlog
    finally:
        try:
            errlog.close()
        except OSError:
            pass
        worker.join(timeout=3)
        try:
            reader.close()
        except OSError:
            pass
