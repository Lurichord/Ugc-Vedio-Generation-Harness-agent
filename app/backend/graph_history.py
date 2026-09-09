"""Studio-only dependency-graph snapshots. One version per graph_version change."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ugc_harness.harness.models import ProjectState

_INDEX = "index.json"


def install_hooks() -> None:
    """Record a snapshot whenever Studio (or its MCP child) persists project_state."""

    from ugc_harness.harness import repair
    from ugc_harness.shared import artifacts

    repair.on_project_state_written = record_graph_snapshot
    artifacts.on_project_state_written = record_graph_snapshot


def history_dir(project_dir: str | Path) -> Path:
    return Path(project_dir) / "harness" / "graph_history"


def record_graph_snapshot(project_dir: str | Path, state: Any) -> int | None:
    """Write version N+1 if the graph exists and graph_version moved."""

    parsed = _as_project_state(state)
    if parsed is None:
        return None
    graph = parsed.dependency_graph
    if not graph.nodes:
        return None
    directory = Path(project_dir)
    index = load_history_index(directory)
    last = index["versions"][-1] if index["versions"] else None
    if last is not None and last["graph_version"] == graph.graph_version:
        return None
    version = int(last["version"]) + 1 if last is not None else 1
    recorded_at = datetime.now(timezone.utc).isoformat()
    folder = history_dir(directory)
    folder.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": version,
        "graph_version": graph.graph_version,
        "recorded_at": recorded_at,
        "dependency_graph": graph.model_dump(mode="json"),
        "production": parsed.production.model_dump(mode="json"),
    }
    (folder / f"{version}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    index["versions"].append(
        {
            "version": version,
            "graph_version": graph.graph_version,
            "recorded_at": recorded_at,
        }
    )
    _write_index(directory, index)
    return version


def load_history_index(project_dir: str | Path) -> dict[str, Any]:
    path = history_dir(project_dir) / _INDEX
    if not path.is_file():
        return {"versions": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"versions": []}
    versions = payload.get("versions")
    if not isinstance(versions, list):
        return {"versions": []}
    cleaned = []
    for item in versions:
        if not isinstance(item, dict):
            continue
        version = item.get("version")
        if not isinstance(version, int):
            continue
        cleaned.append(
            {
                "version": version,
                "graph_version": item.get("graph_version"),
                "recorded_at": item.get("recorded_at") or "",
            }
        )
    return {"versions": cleaned}


def load_history_snapshot(project_dir: str | Path, version: int) -> dict[str, Any]:
    path = history_dir(project_dir) / f"{version}.json"
    if not path.is_file():
        raise FileNotFoundError(f"没有历史版本 {version}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"历史版本 {version} 损坏")
    return payload


def _write_index(project_dir: Path, index: dict[str, Any]) -> None:
    folder = history_dir(project_dir)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / _INDEX).write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _as_project_state(value: Any) -> ProjectState | None:
    if isinstance(value, ProjectState):
        return value
    if isinstance(value, BaseModel):
        try:
            return ProjectState.model_validate(value.model_dump(mode="json"))
        except Exception:
            return None
    if isinstance(value, dict):
        try:
            return ProjectState.model_validate(value)
        except Exception:
            return None
    return None
