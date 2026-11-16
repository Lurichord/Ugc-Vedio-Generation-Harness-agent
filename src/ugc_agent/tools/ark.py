from __future__ import annotations

import base64
import json
import mimetypes
import os
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any


def env_value(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def require_env(*names: str) -> str:
    value = env_value(*names)
    if not value:
        joined = " / ".join(names)
        raise ValueError(f"{joined} is missing")
    return value


def join_url(base: str, *parts: str) -> str:
    url = base.rstrip("/")
    for part in parts:
        url = f"{url}/{part.strip('/')}"
    return url


def data_url(path: str | Path) -> str:
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(path)
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def request_json(
    url: str,
    *,
    api_key: str,
    payload: dict[str, Any] | None = None,
    method: str = "POST",
    timeout: int = 120,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ark http {exc.code}: {detail}") from exc
    if not raw:
        return {}
    return json.loads(raw)


def download_bytes(url: str, *, timeout: int = 120) -> bytes:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"download http {exc.code}: {detail}") from exc


def unique_path(directory: Path, suffix: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{uuid.uuid4().hex[:12]}{suffix}"


def write_unique_file(directory: Path, suffix: str, data: bytes) -> Path:
    path = unique_path(directory, suffix)
    path.write_bytes(data)
    return path
