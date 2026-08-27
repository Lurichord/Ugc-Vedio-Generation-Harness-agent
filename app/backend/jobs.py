from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Lock, Semaphore
from typing import Any, Callable
from uuid import uuid4


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = Lock()
        self._generation_lock = Semaphore(1)
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ugc-web")

    def submit(self, label: str, operation: Callable[[], Any]) -> dict[str, Any]:
        job_id = f"job_{uuid4().hex}"
        job = {
            "job_id": job_id,
            "label": label,
            "status": "queued",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "started_at": None,
            "finished_at": None,
            "result": None,
            "error": None,
        }
        with self._lock:
            self._jobs[job_id] = job
        self._executor.submit(self._execute, job_id, operation)
        return dict(job)

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            return dict(self._jobs[job_id])

    def _execute(self, job_id: str, operation: Callable[[], Any]) -> None:
        with self._generation_lock:
            self._update(job_id, status="running", started_at=datetime.now(timezone.utc).isoformat())
            try:
                result = operation()
                self._update(
                    job_id, status="completed", result=result,
                    finished_at=datetime.now(timezone.utc).isoformat(),
                )
            except Exception as exc:
                self._update(
                    job_id, status="failed", error=str(exc),
                    finished_at=datetime.now(timezone.utc).isoformat(),
                )

    def _update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            self._jobs[job_id].update(changes)

