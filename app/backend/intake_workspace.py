"""App-only intake workspace: project binding, stage gates, studio notices."""

from __future__ import annotations

from pathlib import Path
from threading import Lock
from uuid import uuid4

from .schemas import IntakeNotice, IntakeWorkspaceState, PendingGate


class IntakeWorkspaceStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = Lock()

    def path_for(self, session_id: str) -> Path:
        return self.root / f"{session_id}.workspace.json"

    def load(self, session_id: str) -> IntakeWorkspaceState:
        path = self.path_for(session_id)
        if not path.is_file():
            return IntakeWorkspaceState(session_id=session_id)
        return IntakeWorkspaceState.model_validate_json(
            path.read_text(encoding="utf-8")
        )

    def save(self, workspace: IntakeWorkspaceState) -> None:
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.path_for(workspace.session_id)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(
                workspace.model_dump_json(indent=2),
                encoding="utf-8",
            )
            temporary.replace(path)

    def add_notice(
        self,
        workspace: IntakeWorkspaceState,
        content: str,
        *,
        role: str = "studio",
    ) -> IntakeWorkspaceState:
        notice = IntakeNotice(
            notice_id=f"notice_{uuid4().hex}",
            role=role,  # type: ignore[arg-type]
            content=content,
        )
        notices = list(workspace.notices)
        notices.append(notice)
        updated = workspace.model_copy(update={"notices": notices})
        self.save(updated)
        return updated


def set_gate(
    workspace: IntakeWorkspaceState,
    gate: PendingGate | None,
) -> IntakeWorkspaceState:
    return workspace.model_copy(update={"pending_gate": gate})
