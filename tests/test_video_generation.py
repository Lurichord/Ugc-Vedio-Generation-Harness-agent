from __future__ import annotations

import json
from pathlib import Path

from ugc_harness.shared.settings import AssetGenerationSettings
from ugc_harness.shared.video_generation import generate_seedance_video


class _Response:
    def __init__(
        self,
        payload: dict[str, object] | None = None,
        *,
        status_code: int = 200,
        content: bytes = b"",
        content_type: str = "application/json",
    ) -> None:
        self._payload = payload or {}
        self.status_code = status_code
        self.content = content
        self.headers = {"content-type": content_type}

    def json(self) -> dict[str, object]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _Client:
    def __init__(self) -> None:
        self.posts: list[dict[str, object]] = []
        self._jobs = 0

    def post(self, url: str, headers=None, json=None, timeout=None) -> _Response:
        del headers, timeout
        self.posts.append({"url": url, "json": json})
        self._jobs += 1
        return _Response({"id": f"job-{self._jobs}", "status": "queued"})

    def get(self, url: str, headers=None, timeout=None) -> _Response:
        del headers, timeout
        if "/contents/generations/tasks/" in url:
            job_id = url.rstrip("/").rsplit("/", 1)[-1]
            return _Response(
                {
                    "id": job_id,
                    "status": "succeeded",
                    "content": {"video_url": "https://cdn.test/v.mp4"},
                }
            )
        return _Response(content=b"mp4-bytes", content_type="video/mp4")


def _settings() -> AssetGenerationSettings:
    return AssetGenerationSettings(
        video_api_key="test-key-1234",
        video_base_url="https://ark.test/api/v3",
        video_poll_seconds=0,
        video_timeout_seconds=10,
    )


def _payload(**updates: object) -> dict[str, object]:
    data: dict[str, object] = {
        "model": "doubao-seedance-2-0-260128",
        "prompt": "画面无任何字幕。第一版。",
        "aspect_ratio": "9:16",
        "duration": 4,
        "frame_images": [
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,aaa"},
                "role": "reference_image",
            }
        ],
    }
    data.update(updates)
    return data


def _write_progress(
    path: Path,
    *,
    model: str = "doubao-seedance-2-0-260128",
    job_id: str = "job-old",
    fingerprint: str | None = None,
) -> None:
    record: dict[str, object] = {
        "model": model,
        "job": {
            "id": job_id,
            "status": "succeeded",
            "content": {"video_url": "https://cdn.test/v.mp4"},
        },
    }
    if fingerprint is not None:
        record["request_fingerprint"] = fingerprint
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")


def test_same_fingerprint_resumes_existing_job(tmp_path: Path) -> None:
    client = _Client()
    progress = tmp_path / "shot.json"
    first = generate_seedance_video(
        client, _settings(), _payload(), progress_path=progress
    )
    assert len(client.posts) == 1
    assert first.job_id == "job-1"
    record = json.loads(progress.read_text(encoding="utf-8"))
    assert record["request_fingerprint"]

    second = generate_seedance_video(
        client, _settings(), _payload(), progress_path=progress
    )
    assert len(client.posts) == 1
    assert second.job_id == "job-1"


def test_changed_prompt_submits_a_new_job(tmp_path: Path) -> None:
    client = _Client()
    progress = tmp_path / "shot.json"
    generate_seedance_video(client, _settings(), _payload(), progress_path=progress)
    changed = generate_seedance_video(
        client,
        _settings(),
        _payload(prompt="画面无任何字幕。修片后的第二版。"),
        progress_path=progress,
    )
    assert len(client.posts) == 2
    assert changed.job_id == "job-2"


def test_reference_audio_is_forwarded_in_ark_payload(tmp_path: Path) -> None:
    client = _Client()
    generate_seedance_video(
        client,
        _settings(),
        _payload(
            generate_audio=True,
            audio_urls=[
                {
                    "type": "audio_url",
                    "audio_url": {"url": "data:audio/wav;base64,bbb"},
                    "role": "reference_audio",
                }
            ],
        ),
        progress_path=tmp_path / "shot.json",
    )
    body = client.posts[0]["json"]
    assert body["generate_audio"] is True
    audio = next(item for item in body["content"] if item["type"] == "audio_url")
    assert audio["role"] == "reference_audio"
    assert audio["audio_url"]["url"].startswith("data:audio/wav")
    images = [item for item in body["content"] if item["type"] == "image_url"]
    assert images
    assert all(item.get("role") == "reference_image" for item in images)


def test_changed_reference_audio_submits_a_new_job(tmp_path: Path) -> None:
    client = _Client()
    progress = tmp_path / "shot.json"
    first = _payload(
        generate_audio=True,
        audio_urls=[
            {
                "type": "audio_url",
                "audio_url": {"url": "data:audio/wav;base64,aaa"},
                "role": "reference_audio",
            }
        ],
    )
    generate_seedance_video(client, _settings(), first, progress_path=progress)
    second = generate_seedance_video(
        client,
        _settings(),
        _payload(
            generate_audio=True,
            audio_urls=[
                {
                    "type": "audio_url",
                    "audio_url": {"url": "data:audio/wav;base64,bbb"},
                    "role": "reference_audio",
                }
            ],
        ),
        progress_path=progress,
    )
    assert len(client.posts) == 2
    assert second.job_id == "job-2"


def test_replace_existing_job_submits_even_when_fingerprint_matches(
    tmp_path: Path,
) -> None:
    client = _Client()
    progress = tmp_path / "shot.json"
    generate_seedance_video(client, _settings(), _payload(), progress_path=progress)
    remade = generate_seedance_video(
        client,
        _settings(),
        _payload(),
        progress_path=progress,
        replace_existing_job=True,
    )
    assert len(client.posts) == 2
    assert remade.job_id == "job-2"


def test_legacy_progress_without_fingerprint_still_resumes(tmp_path: Path) -> None:
    client = _Client()
    progress = tmp_path / "shot.json"
    _write_progress(progress)
    generated = generate_seedance_video(
        client, _settings(), _payload(), progress_path=progress
    )
    assert client.posts == []
    assert generated.job_id == "job-old"
