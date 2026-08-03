from __future__ import annotations

import base64
import hashlib
import html
import ipaddress
import json
import re
import subprocess
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from ...shared.settings import AssetGenerationSettings, LLMSettings
from ..editorial_agent.models import ExplorationDirection
from ..voice_agent.models import RealizedBeat
from .models import AssetCard, AssetUsabilityReview, SourceTrace


@dataclass(frozen=True)
class ProviderResult:
    asset: AssetCard | None
    status: str
    reason: str


class _MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title: str | None = None
        self.image_url: str | None = None
        self._in_title = False

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        values = {key.lower(): value for key, value in attrs if value}
        if tag.lower() == "title":
            self._in_title = True
        if tag.lower() == "meta":
            key = (values.get("property") or values.get("name") or "").lower()
            if key in {"og:image", "twitter:image"} and not self.image_url:
                self.image_url = values.get("content")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title and data.strip():
            self.title = (self.title or "") + data.strip()


class OpenRouterWebAssetProvider:
    """Acquire one web asset per direction; never returns a candidate list."""

    _WEB_TYPES = {
        "source_screenshot",
        "document_screenshot",
        "chart",
        "real_image",
        "meme",
    }

    def __init__(
        self,
        settings: LLMSettings,
        *,
        edge_path: str | Path | None = None,
    ):
        self.settings = settings
        self.client = httpx.Client(
            timeout=settings.timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "ugc-video-harness/0.4"},
        )
        self.edge_path = Path(edge_path) if edge_path else _find_edge()

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "OpenRouterWebAssetProvider":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def acquire(
        self,
        *,
        project_id: str,
        visual_request_id: str,
        beat: RealizedBeat,
        direction: ExplorationDirection,
        project_dir: Path,
        **_: object,
    ) -> ProviderResult:
        if direction.asset_type not in self._WEB_TYPES:
            return ProviderResult(
                None,
                "not_supported",
                f"当前 Provider 不支持自动获取 {direction.asset_type}",
            )
        try:
            source = self._search_one_source(direction, beat)
            if source is None:
                return ProviderResult(None, "not_found", "Web Search 未返回来源")
            url = source["url"]
            _validate_public_url(url)
            asset_id = f"asset_{visual_request_id}"
            folder = project_dir / "assets" / direction.asset_type
            folder.mkdir(parents=True, exist_ok=True)

            if direction.asset_type in {"real_image", "meme"}:
                output, mime = self._download_page_image(
                    url, folder / f"{asset_id}"
                )
                origin = "downloaded"
            else:
                if self.edge_path is None:
                    return ProviderResult(
                        None,
                        "not_supported",
                        "未找到 Microsoft Edge，无法捕获网页截图",
                    )
                output = folder / f"{asset_id}.png"
                self._capture_page(url, output)
                mime = "image/png"
                origin = "captured"

            review = self._review_web_asset(output, mime)
            if not review.usable:
                output.unlink(missing_ok=True)
                return ProviderResult(
                    None,
                    "not_found",
                    "素材被登录/注册/验证码或认证界面遮挡："
                    + review.reason,
                )

            digest = hashlib.sha256(output.read_bytes()).hexdigest()
            card = AssetCard(
                asset_id=asset_id,
                visual_request_id=visual_request_id,
                direction_id=direction.direction_id,
                beat_id=beat.beat_id,
                modality=direction.asset_type,
                origin=origin,
                local_path=output.relative_to(project_dir).as_posix(),
                mime_type=mime,
                sha256=digest,
                source=SourceTrace(
                    source_url=url,
                    title=source.get("title"),
                    publisher=source.get("publisher"),
                ),
                generated_media_disclosure_required=False,
                usability_review=review,
            )
            return ProviderResult(card, "success", "已获取第一份合格素材")
        except Exception as exc:
            return ProviderResult(None, "error", str(exc)[:500])

    def _review_web_asset(
        self,
        image_path: Path,
        mime_type: str,
    ) -> AssetUsabilityReview:
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        response = self.client.post(
            self.settings.base_url.rstrip("/") + "/chat/completions",
            headers=_auth_headers(self.settings.api_key),
            json={
                "model": self.settings.model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "你是网页素材可用性审查器。只判断登录、注册、验证码、"
                            "年龄确认、访问认证或订阅登录墙是否遮住了用户真正需要的"
                            "主体内容。普通导航栏中的登录按钮不算遮挡；不遮住主体的"
                            "小型 Cookie 提示不算失败。只返回 JSON。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "审查这张 Web 素材。若认证类弹窗、整页登录墙或遮罩"
                                    "导致主体无法直接使用，则 usable=false。返回："
                                    '{"usable":true,"login_or_auth_overlay":false,'
                                    '"obstruction_level":"none|minor|major|full",'
                                    '"reason":"简短原因"}'
                                ),
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{encoded}"
                                },
                            },
                        ],
                    },
                ],
            },
        )
        response.raise_for_status()
        payload = response.json()
        text = payload["choices"][0]["message"].get("content") or ""
        return _parse_asset_review(text, self.settings.model)

    def _search_one_source(
        self,
        direction: ExplorationDirection,
        beat: RealizedBeat,
    ) -> dict[str, str] | None:
        endpoint = (
            self.settings.base_url.rstrip("/") + "/chat/completions"
        )
        prompt = f"""为一个 UGC 视频视觉方向寻找一份公开网页来源。
只选择一个最匹配且无需登录即可访问的来源，不要返回候选列表。
旁白：{beat.narration}
视觉方向：{direction.description}
查询：{direction.query or direction.description}
素材类型：{direction.asset_type}

只返回 JSON：
{{"url":"https://...","title":"页面标题","publisher":"发布者"}}
如果找不到，返回 {{"url":"","title":"","publisher":""}}。
不需要判断来源是否证明旁白，只负责寻找相关素材页面。"""
        response = self.client.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.settings.model,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
                "tools": [{"type": "openrouter:web_search"}],
                "messages": [
                    {
                        "role": "system",
                        "content": "只返回一个来源，不进行事实核验。",
                    },
                    {"role": "user", "content": prompt},
                ],
            },
        )
        response.raise_for_status()
        payload = response.json()
        text = payload["choices"][0]["message"].get("content") or ""
        value = _parse_json_object(text)
        url = str(value.get("url") or "").strip()
        if not url:
            return None
        return {
            "url": url,
            "title": str(value.get("title") or "").strip() or None,
            "publisher": (
                str(value.get("publisher") or "").strip() or None
            ),
        }

    def _download_page_image(
        self,
        page_url: str,
        output_stem: Path,
    ) -> tuple[Path, str]:
        page = self.client.get(page_url)
        page.raise_for_status()
        content_type = page.headers.get("content-type", "").split(";")[0]
        if content_type.startswith("image/"):
            image_url = str(page.url)
            image_response = page
        else:
            parser = _MetadataParser()
            parser.feed(page.text[:2_000_000])
            if not parser.image_url:
                raise RuntimeError("来源页面没有可下载的 og:image")
            image_url = urljoin(str(page.url), html.unescape(parser.image_url))
            _validate_public_url(image_url)
            image_response = self.client.get(image_url)
            image_response.raise_for_status()

        if len(image_response.content) > 20 * 1024 * 1024:
            raise RuntimeError("图片超过 20MB 限制")
        mime = image_response.headers.get("content-type", "").split(";")[0]
        if not mime.startswith("image/"):
            raise RuntimeError(f"下载内容不是图片: {mime or 'unknown'}")
        suffix = _image_suffix(mime)
        output = output_stem.with_suffix(suffix)
        output.write_bytes(image_response.content)
        if output.stat().st_size < 1024:
            output.unlink(missing_ok=True)
            raise RuntimeError("下载图片过小")
        return output, mime

    def _capture_page(self, url: str, output: Path) -> None:
        assert self.edge_path is not None
        command = [
            str(self.edge_path),
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--window-size=1080,1920",
            f"--screenshot={output.resolve()}",
            url,
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=False,
            timeout=45,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0 or not output.is_file():
            raw_output = completed.stderr or completed.stdout or b"unknown error"
            raise RuntimeError(
                "网页截图失败: "
                + raw_output.decode("utf-8", errors="replace")[-300:]
            )


def _find_edge() -> Path | None:
    candidates = [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ]
    return next((path for path in candidates if path.is_file()), None)


def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("只允许公开 HTTP/HTTPS URL")
    host = parsed.hostname.lower()
    if host in {"localhost"} or host.endswith(".local"):
        raise ValueError("禁止访问本机或内网地址")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    else:
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
        ):
            raise ValueError("禁止访问本机或内网地址")


def _parse_json_object(text: str) -> dict:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Web Search 没有返回 JSON")
    value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("Web Search 返回值不是对象")
    return value


def _parse_asset_review(
    text: str,
    reviewer_model: str,
) -> AssetUsabilityReview:
    value = _parse_json_object(text)
    return AssetUsabilityReview(
        reviewer_model=reviewer_model,
        usable=value.get("usable"),
        login_or_auth_overlay=value.get("login_or_auth_overlay"),
        obstruction_level=value.get("obstruction_level"),
        reason=str(value.get("reason") or "").strip(),
    )


def _image_suffix(mime: str) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(mime.lower(), ".img")


class OpenRouterGeneratedAssetProvider:
    """Generate non-web visual directions as disclosed AI media."""

    _IMAGE_TYPES = {
        "kinetic_typography",
        "motion_graphic",
        "ai_image",
        "screen_recording",
    }

    def __init__(
        self,
        llm_settings: LLMSettings,
        generation_settings: AssetGenerationSettings,
    ) -> None:
        self.llm_settings = llm_settings
        self.generation_settings = generation_settings
        self.client = httpx.Client(
            timeout=llm_settings.timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "ugc-video-harness/0.4"},
        )

    def close(self) -> None:
        self.client.close()

    def acquire(
        self,
        *,
        project_id: str,
        visual_request_id: str,
        beat: RealizedBeat,
        direction: ExplorationDirection,
        project_dir: Path,
        character_id: str | None = None,
        character_description: str | None = None,
        continuity_group_id: str | None = None,
        previous_character_asset: AssetCard | None = None,
    ) -> ProviderResult:
        del project_id
        try:
            prompt = _generation_prompt(beat, direction)
            if direction.asset_type == "talking_head":
                if not character_id or not character_description:
                    raise ValueError("talking_head requires a world-state character")
                return self._generate_talking_head_video(
                    visual_request_id=visual_request_id,
                    beat=beat,
                    direction=direction,
                    project_dir=project_dir,
                    character_id=character_id,
                    character_description=character_description,
                    continuity_group_id=continuity_group_id,
                    previous_character_asset=previous_character_asset,
                )
            if direction.asset_type == "ai_video":
                return self._generate_video(
                    visual_request_id, beat, direction, project_dir, prompt
                )
            if direction.asset_type in self._IMAGE_TYPES:
                return self._generate_image(
                    visual_request_id, beat, direction, project_dir, prompt
                )
            return ProviderResult(
                None,
                "not_supported",
                f"AI Provider 不支持素材类型 {direction.asset_type}",
            )
        except Exception as exc:
            return ProviderResult(None, "error", str(exc)[:500])

    def _generate_talking_head_video(
        self,
        *,
        visual_request_id: str,
        beat: RealizedBeat,
        direction: ExplorationDirection,
        project_dir: Path,
        character_id: str,
        character_description: str,
        continuity_group_id: str | None,
        previous_character_asset: AssetCard | None,
    ) -> ProviderResult:
        reference = self._character_reference(
            character_id, character_description, project_dir
        )
        previous_asset_id = None
        if previous_character_asset is not None:
            first_frame = self._last_frame(
                project_dir / previous_character_asset.local_path,
                project_dir,
                previous_character_asset.asset_id,
            )
            previous_asset_id = previous_character_asset.asset_id
            continuity_instruction = (
                "Continue the exact body pose, hand position, gaze, camera, and "
                "motion from the supplied first frame. Movement must begin smoothly."
            )
        else:
            first_frame = reference
            continuity_instruction = (
                "Begin a fresh natural gesture while preserving the exact identity, "
                "wardrobe, room, camera, and lighting from the reference frame."
            )
        encoded = base64.b64encode(first_frame.read_bytes()).decode("ascii")
        duration = min((4, 6, 8), key=lambda item: abs(item - beat.duration_ms / 1000))
        prompt = f"""Vertical authentic phone-camera creator video.
Character identity: {character_description}
Spoken content context: {beat.narration}
{continuity_instruction}
Natural blinking, breathing, subtle head and hand gestures, realistic posture shifts.
One continuous take, direct eye contact, no cuts, no text, no logos, no audio.
Do not change face, age, hair, clothing, background, lens, framing, or lighting."""
        payload = {
            "model": self.generation_settings.video_model,
            "prompt": prompt,
            "resolution": self.generation_settings.video_resolution,
            "aspect_ratio": "9:16",
            "duration": duration,
            "generate_audio": False,
            "frame_images": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{encoded}"},
                    "frame_type": "first_frame",
                }
            ],
        }
        return self._submit_video(
            visual_request_id,
            beat,
            direction,
            project_dir,
            prompt,
            payload,
            character_id=character_id,
            continuity_group_id=continuity_group_id,
            previous_asset_id=previous_asset_id,
            identity_reference_path=reference.relative_to(project_dir).as_posix(),
            modality="talking_head",
        )

    def _character_reference(
        self,
        character_id: str,
        description: str,
        project_dir: Path,
    ) -> Path:
        folder = project_dir / "assets" / "character_reference"
        folder.mkdir(parents=True, exist_ok=True)
        output = folder / f"{character_id}.png"
        if output.is_file() and output.stat().st_size:
            return output
        response = self.client.post(
            self.llm_settings.base_url.rstrip("/") + "/images",
            headers=_auth_headers(self.llm_settings.api_key),
            json={
                "model": self.generation_settings.image_model,
                "prompt": (
                    "Vertical 9:16 identity reference portrait for a UGC creator. "
                    f"{description}. Direct eye contact, neutral relaxed pose, phone "
                    "camera realism, clear face and hands, no text or logos."
                ),
                "n": 1,
                "aspect_ratio": "9:16",
                "resolution": "1K",
                "output_format": "png",
            },
        )
        response.raise_for_status()
        raw = base64.b64decode(response.json()["data"][0]["b64_json"], validate=True)
        output.write_bytes(raw)
        return output

    @staticmethod
    def _last_frame(video: Path, project_dir: Path, asset_id: str) -> Path:
        output = project_dir / "assets" / "character_reference" / f"{asset_id}_last.png"
        repository = Path(__file__).resolve().parents[4]
        ffmpeg = repository / "renderer" / "node_modules" / "@remotion" / "compositor-win32-x64-msvc" / "ffmpeg.exe"
        completed = subprocess.run(
            [str(ffmpeg), "-y", "-sseof", "-0.05", "-i", str(video), "-frames:v", "1", str(output)],
            capture_output=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0 or not output.is_file():
            raise RuntimeError("failed to extract previous talking-head last frame")
        return output

    def _generate_image(
        self,
        visual_request_id: str,
        beat: RealizedBeat,
        direction: ExplorationDirection,
        project_dir: Path,
        prompt: str,
    ) -> ProviderResult:
        response = self.client.post(
            self.llm_settings.base_url.rstrip("/") + "/images",
            headers=_auth_headers(self.llm_settings.api_key),
            json={
                "model": self.generation_settings.image_model,
                "prompt": prompt,
                "n": 1,
                "aspect_ratio": "9:16",
                "resolution": "1K",
                "output_format": "png",
            },
        )
        response.raise_for_status()
        payload = response.json()
        item = payload["data"][0]
        raw = base64.b64decode(item["b64_json"], validate=True)
        if not raw or len(raw) > 30 * 1024 * 1024:
            raise RuntimeError("AI 图片为空或超过 30MB")
        mime = item.get("media_type") or "image/png"
        asset_id = f"asset_{visual_request_id}"
        folder = project_dir / "assets" / "generated_image"
        folder.mkdir(parents=True, exist_ok=True)
        output = folder / f"{asset_id}{_image_suffix(mime)}"
        output.write_bytes(raw)
        card = AssetCard(
            asset_id=asset_id,
            visual_request_id=visual_request_id,
            direction_id=direction.direction_id,
            beat_id=beat.beat_id,
            modality="ai_image",
            origin="generated",
            local_path=output.relative_to(project_dir).as_posix(),
            mime_type=mime,
            sha256=hashlib.sha256(raw).hexdigest(),
            generated_media_disclosure_required=True,
            generator_model=self.generation_settings.image_model,
            generation_prompt=prompt,
            generation_cost_usd=(payload.get("usage") or {}).get("cost"),
        )
        return ProviderResult(card, "success", "已生成一份 AI 图片素材")

    def _generate_video(
        self,
        visual_request_id: str,
        beat: RealizedBeat,
        direction: ExplorationDirection,
        project_dir: Path,
        prompt: str,
    ) -> ProviderResult:
        payload = {
            "model": self.generation_settings.video_model,
            "prompt": prompt,
            "resolution": self.generation_settings.video_resolution,
            "aspect_ratio": "9:16",
            "generate_audio": False,
        }
        return self._submit_video(
            visual_request_id,
            beat,
            direction,
            project_dir,
            prompt,
            payload,
        )

    def _submit_video(
        self,
        visual_request_id: str,
        beat: RealizedBeat,
        direction: ExplorationDirection,
        project_dir: Path,
        prompt: str,
        payload: dict[str, object],
        *,
        character_id: str | None = None,
        continuity_group_id: str | None = None,
        previous_asset_id: str | None = None,
        identity_reference_path: str | None = None,
        modality: str = "ai_video",
    ) -> ProviderResult:
        response = self.client.post(
            self.llm_settings.base_url.rstrip("/") + "/videos",
            headers=_auth_headers(self.llm_settings.api_key),
            json=payload,
        )
        response.raise_for_status()
        job = response.json()
        job_id = str(job["id"])
        polling_url = str(job.get("polling_url") or (
            self.llm_settings.base_url.rstrip("/") + f"/videos/{job_id}"
        ))
        deadline = time.monotonic() + self.generation_settings.video_timeout_seconds
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"AI 视频任务 {job_id} 超时")
            time.sleep(self.generation_settings.video_poll_seconds)
            status_response = self.client.get(
                polling_url,
                headers=_auth_headers(self.llm_settings.api_key),
            )
            status_response.raise_for_status()
            job = status_response.json()
            status = job.get("status")
            if status == "completed":
                break
            if status in {"failed", "cancelled", "expired"}:
                raise RuntimeError(
                    f"AI 视频任务 {status}: {job.get('error') or 'unknown error'}"
                )
        urls = job.get("unsigned_urls") or []
        content_url = urls[0] if urls else (
            self.llm_settings.base_url.rstrip("/")
            + f"/videos/{job_id}/content?index=0"
        )
        content = self.client.get(
            content_url,
            headers=_auth_headers(self.llm_settings.api_key),
            timeout=120,
        )
        content.raise_for_status()
        raw = content.content
        if not raw or len(raw) > 200 * 1024 * 1024:
            raise RuntimeError("AI 视频为空或超过 200MB")
        asset_id = f"asset_{visual_request_id}"
        folder = project_dir / "assets" / "generated_video"
        folder.mkdir(parents=True, exist_ok=True)
        output = folder / f"{asset_id}.mp4"
        output.write_bytes(raw)
        card = AssetCard(
            asset_id=asset_id,
            visual_request_id=visual_request_id,
            direction_id=direction.direction_id,
            beat_id=beat.beat_id,
            modality=modality,
            origin="generated",
            local_path=output.relative_to(project_dir).as_posix(),
            mime_type=content.headers.get("content-type", "video/mp4").split(";")[0],
            sha256=hashlib.sha256(raw).hexdigest(),
            generated_media_disclosure_required=True,
            generator_model=self.generation_settings.video_model,
            generation_prompt=prompt,
            generation_job_id=job_id,
            generation_cost_usd=(job.get("usage") or {}).get("cost"),
            character_id=character_id,
            continuity_group_id=continuity_group_id,
            previous_asset_id=previous_asset_id,
            identity_reference_path=identity_reference_path,
        )
        return ProviderResult(card, "success", "已生成一份动态视频素材")


class RoutedAssetProvider:
    """Web only for source imagery; every other direction uses generation."""

    def __init__(
        self,
        llm_settings: LLMSettings,
        generation_settings: AssetGenerationSettings,
    ) -> None:
        self.web = OpenRouterWebAssetProvider(llm_settings)
        self.generated = OpenRouterGeneratedAssetProvider(
            llm_settings, generation_settings
        )

    def __enter__(self) -> "RoutedAssetProvider":
        return self

    def __exit__(self, *_: object) -> None:
        self.web.close()
        self.generated.close()

    def acquire(self, **kwargs: object) -> ProviderResult:
        direction = kwargs["direction"]
        if not isinstance(direction, ExplorationDirection):
            raise TypeError("direction must be ExplorationDirection")
        if direction.asset_type in OpenRouterWebAssetProvider._WEB_TYPES:
            return self.web.acquire(**kwargs)
        return self.generated.acquire(**kwargs)


def _auth_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _generation_prompt(
    beat: RealizedBeat,
    direction: ExplorationDirection,
) -> str:
    special = {
        "screen_recording": (
            "Create a conceptual UI screen suitable for later screen-recording "
            "animation. It must look like a clearly generic mockup, not a real "
            "product screenshot."
        ),
        "motion_graphic": (
            "Create a polished motion-graphics keyframe with clear visual hierarchy "
            "and simple shapes that can be animated later."
        ),
        "kinetic_typography": (
            "Create a bold kinetic-typography keyframe with very little readable text."
        ),
        "ai_video": (
            "Create a natural short vertical UGC insert with one coherent action, "
            "subtle camera motion, and no cinematic film look."
        ),
        "talking_head": (
            "Create a casual creator-style talking-head visual, authentic phone-camera "
            "lighting, direct eye contact, and no celebrity likeness."
        ),
    }.get(direction.asset_type, "Create a clear illustrative vertical UGC visual.")
    constraints = "；".join(direction.must_not_imply) or "不得伪装成事实记录"
    return f"""Vertical 9:16 asset for a 1–2 minute UGC explainer, not a film.
Narration context: {beat.narration}
Visual direction: {direction.description}
Generation/search instruction: {direction.query or 'none'}
Purpose: {direction.visual_role}
{special}
Avoid logos, watermarks, fake citations, fake news pages, and dense small text.
Do not imply: {constraints}
Generated media is illustrative and must never be presented as factual evidence."""
