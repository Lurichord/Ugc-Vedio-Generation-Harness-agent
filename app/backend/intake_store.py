from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

from ugc_harness.intake.models import IntakeSession


class ProjectSessionIndex:
    """Maps a project folder name to the intake session that owns its chat."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()

    def session_id(self, project_key: str) -> str | None:
        with self._lock:
            bound = self._read_unlocked().get(project_key)
        return bound if isinstance(bound, str) and bound else None

    def bind(self, project_key: str, session_id: str) -> None:
        with self._lock:
            data = {
                key: value
                for key, value in self._read_unlocked().items()
                if value != session_id
            }
            data[project_key] = session_id
            self._write_unlocked(data)

    def unbind(self, project_key: str) -> None:
        with self._lock:
            data = self._read_unlocked()
            if project_key not in data:
                return
            data.pop(project_key, None)
            self._write_unlocked(data)

    def _read_unlocked(self) -> dict[str, str]:
        if not self.path.is_file():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {
            str(key): str(value)
            for key, value in payload.items()
            if isinstance(key, str) and isinstance(value, str) and value
        }

    def _write_unlocked(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)


class IntakeStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = Lock()

    def save(self, session: IntakeSession) -> None:
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.root / f"{session.session_id}.json"
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(
                session.model_dump_json(indent=2),
                encoding="utf-8",
            )
            temporary.replace(path)

    def path_for(self, session_id: str) -> Path:
        return self.root / f"{session_id}.json"

    def load(self, session_id: str) -> IntakeSession:
        path = self.path_for(session_id)
        if not path.is_file():
            raise FileNotFoundError(session_id)
        return IntakeSession.model_validate_json(path.read_text(encoding="utf-8"))
