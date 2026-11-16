from __future__ import annotations

import os
import threading
from pathlib import Path

from .state import ProjectState


class RevisionConflict(RuntimeError):
    pass


class ProjectStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.RLock()

    def exists(self, project_id: str) -> bool:
        return self._path(project_id).is_file()

    def list_ids(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return [
            path.parent.name
            for path in sorted(self.root.glob("*/project_state.json"))
            if path.is_file()
        ]

    def create(self, project_id: str) -> ProjectState:
        with self._lock:
            if self.exists(project_id):
                raise FileExistsError(project_id)
            (self.root / project_id / "media").mkdir(parents=True, exist_ok=True)
            state = ProjectState(project_id=project_id)
            self._path(project_id).write_text(
                state.model_dump_json(indent=2),
                encoding="utf-8",
            )
            return state

    def load(self, project_id: str) -> ProjectState:
        with self._lock:
            path = self._path(project_id)
            if not path.exists():
                return ProjectState(project_id=project_id)
            return ProjectState.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, state: ProjectState) -> ProjectState:
        with self._lock:
            current = self.load(state.project_id)
            if current.revision != state.revision:
                raise RevisionConflict(
                    f"expected revision {state.revision}, got {current.revision}"
                )
            saved = state.model_copy(update={"revision": current.revision + 1})
            path = self._path(state.project_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(
                saved.model_dump_json(indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, path)
            return saved

    def _path(self, project_id: str) -> Path:
        return self.root / project_id / "project_state.json"
