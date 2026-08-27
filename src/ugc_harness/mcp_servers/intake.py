"""Intent-layer MCP server. Isolated from narrative / stage execution tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from ..intake.actions import (
    execute_start_project,
    get_description_element,
    inspect_progress,
    list_dependency_graph,
    list_description_outline,
    repair_description,
    start_project,
)
from ..intake.models import IntakeSession
from ..intake.tools import (
    CONFIGURE_SESSION,
    DESCRIPTION_GET_ELEMENT,
    DESCRIPTION_LIST_OUTLINE,
    HARNESS_INSPECT,
    HARNESS_LIST_GRAPH,
    HARNESS_REPAIR,
    HARNESS_START_PROJECT,
    INTAKE_MCP_TOOLS,
)

CONFIGURE_TOOL = CONFIGURE_SESSION

_session_path: Path | None = None
_dry_run = False
_output_root: Path | None = None


mcp = MCPServer(
    "ugc-intake",
    version="1.0.0",
    instructions=(
        "Intent-layer tools only: read one description element or a slim "
        "dependency graph, then dispatch harness start/inspect/repair. Does "
        "not write working_brief and does not expose narrative.* or stage "
        "submit_candidate tools."
    ),
)


def reset() -> None:
    global _session_path, _dry_run, _output_root
    _session_path = None
    _dry_run = False
    _output_root = None


def _load_session() -> IntakeSession:
    if _session_path is None:
        raise RuntimeError("intake.configure_session must be called first")
    return IntakeSession.model_validate_json(
        _session_path.read_text(encoding="utf-8")
    )


def _save_session(session: IntakeSession) -> None:
    if _session_path is None:
        raise RuntimeError("intake.configure_session must be called first")
    _session_path.parent.mkdir(parents=True, exist_ok=True)
    _session_path.write_text(session.model_dump_json(indent=2), encoding="utf-8")


@mcp.tool(name=CONFIGURE_SESSION, structured_output=True)
def configure_session(
    session_path: str,
    dry_run: bool = False,
    output_root: str | None = None,
) -> dict[str, Any]:
    """Point this process at one IntakeSession JSON file for the current beat."""

    global _session_path, _dry_run, _output_root
    path = Path(session_path)
    if not path.is_file():
        raise FileNotFoundError(session_path)
    _session_path = path
    _dry_run = dry_run
    _output_root = Path(output_root) if output_root else None
    session = _load_session()
    return {
        "ok": True,
        "session_id": session.session_id,
        "tools": list(INTAKE_MCP_TOOLS),
    }


@mcp.tool(name=DESCRIPTION_LIST_OUTLINE, structured_output=True)
def description_list_outline() -> dict[str, Any]:
    return list_description_outline(_load_session())


@mcp.tool(name=DESCRIPTION_GET_ELEMENT, structured_output=True)
def description_get_element(ref: str) -> dict[str, Any]:
    return get_description_element(_load_session(), ref)


@mcp.tool(name=HARNESS_INSPECT, structured_output=True)
def harness_inspect() -> dict[str, Any]:
    session, result = inspect_progress(_load_session())
    _save_session(session)
    return result


@mcp.tool(name=HARNESS_LIST_GRAPH, structured_output=True)
def harness_list_graph(around_ref: str | None = None) -> dict[str, Any]:
    focus = (around_ref or "").strip() or None
    return list_dependency_graph(_load_session(), focus)


@mcp.tool(name=HARNESS_START_PROJECT, structured_output=True)
def harness_start_project() -> dict[str, Any]:
    starter = None
    if _output_root is not None and not _dry_run:
        root = _output_root
        starter = lambda brief: execute_start_project(brief, root)
    session, result = start_project(
        _load_session(),
        start_project=starter,
        dry_run=_dry_run,
    )
    _save_session(session)
    return result


@mcp.tool(name=HARNESS_REPAIR, structured_output=True)
def harness_repair(
    target_refs: list[str],
    instruction: str,
) -> dict[str, Any]:
    session, result = repair_description(
        _load_session(),
        target_refs,
        instruction,
    )
    _save_session(session)
    return result


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
