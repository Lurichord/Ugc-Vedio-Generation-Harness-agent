from __future__ import annotations

import httpx

from ugc_harness.shared.image_generation import generate_seedream_image
from ugc_harness.shared.settings import AssetGenerationSettings


def test_seedream_uses_ark_image_endpoint_and_downloads_result() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/images/generations"):
            return httpx.Response(
                200,
                json={"data": [{"url": "https://media.example/result.jpg"}]},
            )
        return httpx.Response(
            200,
            content=b"fake-jpeg-content",
            headers={"content-type": "image/jpeg"},
        )

    settings = AssetGenerationSettings(
        image_api_key="ark-test-key-123",
        image_base_url="https://ark.example/api/v3",
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = generate_seedream_image(client, settings, "vertical creator")

    assert requests[0].url.path == "/api/v3/images/generations"
    assert result.content == b"fake-jpeg-content"
    assert result.mime_type == "image/jpeg"
