"""VLM provider adapter (Phase O3-1).

OpenAI-compatible vision-language model adapter.
Supports both online APIs (Qwen/DashScope) and local llama.cpp.

Image input formats:
  - HTTP/HTTPS URL: passed directly as image_url
  - Local file path: auto-encoded to data:image/...;base64,...

No LangChain dependency — uses httpx directly to keep the VLM call
isolated from the main LLM agent chain and its KV cache.
"""

import base64
import logging
import mimetypes
import os
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Supported image extensions
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


@dataclass
class VLMResponse:
    """Result of a VLM call."""
    success: bool
    content: str  # model response text or error message
    usage: dict[str, int] | None = None  # token usage if available


def encode_image_to_data_uri(path: str) -> str:
    """Encode a local image file to a data URI string.

    Returns: data:image/<type>;base64,<encoded>
    Raises FileNotFoundError if path doesn't exist.
    Raises ValueError if file type is unsupported.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Image file not found: {path}")

    ext = os.path.splitext(path)[1].lower()
    if ext not in _IMAGE_EXTENSIONS:
        raise ValueError(f"Unsupported image type: {ext}")

    mime_type = mimetypes.guess_type(path)[0] or "image/png"

    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("ascii")

    return f"data:{mime_type};base64,{encoded}"


def build_image_url(image_source: str) -> str:
    """Convert an image source to a URL suitable for OpenAI-compatible APIs.

    - HTTP/HTTPS URLs are passed through
    - Local file paths are encoded to data URIs
    """
    if image_source.startswith(("http://", "https://", "data:")):
        return image_source
    # Treat as local file path
    return encode_image_to_data_uri(image_source)


async def describe_image(
    *,
    image_source: str,
    prompt: str = "Describe this image in detail.",
    base_url: str,
    api_key: str,
    model_id: str,
    timeout: float = 30.0,
    max_tokens: int = 1024,
) -> VLMResponse:
    """Call an OpenAI-compatible VLM to describe an image.

    Args:
        image_source: HTTP URL, data URI, or local file path.
        prompt: Text prompt to send alongside the image.
        base_url: VLM API base URL.
        api_key: API key for the VLM endpoint.
        model_id: Model identifier.
        timeout: Request timeout in seconds.
        max_tokens: Max tokens for the response.

    Returns VLMResponse with success=True and model text, or
    success=False with error message. Never raises.
    """
    try:
        image_url = build_image_url(image_source)
    except (FileNotFoundError, ValueError) as e:
        return VLMResponse(success=False, content=str(e))

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        }
    ]

    payload: dict[str, Any] = {
        "model": model_id,
        "messages": messages,
        "max_tokens": max_tokens,
    }

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    url = base_url.rstrip("/") + "/chat/completions"

    try:
        async with httpx.AsyncClient(trust_env=False, timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)

        if resp.status_code != 200:
            return VLMResponse(
                success=False,
                content=f"VLM API error {resp.status_code}: {resp.text[:500]}",
            )

        body = resp.json()
        choices = body.get("choices", [])
        if not choices:
            return VLMResponse(success=False, content="VLM returned empty choices")

        text = choices[0].get("message", {}).get("content", "")
        usage = body.get("usage")

        return VLMResponse(success=True, content=text, usage=usage)

    except httpx.TimeoutException:
        return VLMResponse(success=False, content="VLM request timed out")
    except Exception as e:
        return VLMResponse(success=False, content=f"VLM call failed: {e}")


async def check_vlm_available(
    base_url: str,
    api_key: str = "",
    timeout: float = 5.0,
) -> bool:
    """Quick probe: check if VLM endpoint responds to /models.

    Returns True if the endpoint is reachable, False otherwise.
    """
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    url = base_url.rstrip("/") + "/models"

    try:
        async with httpx.AsyncClient(trust_env=False, timeout=timeout) as client:
            resp = await client.get(url, headers=headers)
        return resp.status_code == 200
    except Exception:
        return False
