from __future__ import annotations

from pathlib import Path
from threading import Lock

from ugc_harness.intake.models import IntakeSession


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
