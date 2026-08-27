from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from mcp import StdioServerParameters


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
