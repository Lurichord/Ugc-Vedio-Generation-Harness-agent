from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any

from .schemas import ApprovalRecord, FeedbackRecord, TaskEvent


class AppDataRepository:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = Lock()

    def approvals(self, project_id: str) -> list[ApprovalRecord]:
        payload = self._read(project_id, "reviews.json", {"approvals": []})
        return [ApprovalRecord.model_validate(item) for item in payload["approvals"]]

    def save_approval(self, project_id: str, record: ApprovalRecord) -> None:
        with self._lock:
            payload = self._read(project_id, "reviews.json", {"approvals": []})
            payload["approvals"] = [
                item for item in payload["approvals"] if item["stage"] != record.stage
            ]
            payload["approvals"].append(record.model_dump(mode="json"))
            self._write(project_id, "reviews.json", payload)

    def feedback(self, project_id: str) -> list[FeedbackRecord]:
        payload = self._read(project_id, "feedback.json", {"items": []})
        return [FeedbackRecord.model_validate(item) for item in payload["items"]]

    def save_feedback(self, project_id: str, record: FeedbackRecord) -> None:
        with self._lock:
            payload = self._read(project_id, "feedback.json", {"items": []})
            payload["items"].append(record.model_dump(mode="json"))
            self._write(project_id, "feedback.json", payload)

    def update_feedback(self, project_id: str, record: FeedbackRecord) -> None:
        with self._lock:
            payload = self._read(project_id, "feedback.json", {"items": []})
            payload["items"] = [
                record.model_dump(mode="json")
                if item["feedback_id"] == record.feedback_id
                else item
                for item in payload["items"]
            ]
            self._write(project_id, "feedback.json", payload)

    def events(self, project_id: str) -> list[TaskEvent]:
        payload = self._read(project_id, "job_events.json", {"events": []})
        return [TaskEvent.model_validate(item) for item in payload["events"]]

    def add_event(self, project_id: str, event: TaskEvent) -> None:
        with self._lock:
            payload = self._read(project_id, "job_events.json", {"events": []})
            payload["events"].append(event.model_dump(mode="json"))
            self._write(project_id, "job_events.json", payload)

    def _read(self, project_id: str, filename: str, default: Any) -> Any:
        path = self.root / project_id / filename
        if not path.is_file():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def _write(self, project_id: str, filename: str, payload: Any) -> None:
        path = self.root / project_id / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)
