"""Image extraction layer (Phase O3-2).

Collect basic image metadata (dimensions, format, size) and optionally
invoke a VLM for visual reconnaissance.  No heavy dependencies — only
PIL/Pillow for metadata, VLM calls go through vlm.provider.
"""

import logging
import mimetypes
import os
from typing import Any

logger = logging.getLogger(__name__)

# Extensions handled by this module
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def extract_metadata(path: str) -> dict[str, Any]:
    """Extract basic image metadata without any model call.

    Returns dict with kind, image_format, width, height, size_bytes, mime_type.
    Raises FileNotFoundError / ValueError on bad input.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Image file not found: {path}")

    ext = os.path.splitext(path)[1].lower()
    if ext not in IMAGE_EXTENSIONS:
        raise ValueError(f"Unsupported image type: {ext}")

    size_bytes = os.path.getsize(path)
    mime_type = mimetypes.guess_type(path)[0] or f"image/{ext.lstrip('.')}"

    # Dimensions via Pillow (lazy import — only needed here)
    width, height = 0, 0
    image_format = ext.lstrip(".")
    try:
        from PIL import Image
        with Image.open(path) as img:
            width, height = img.size
            image_format = (img.format or ext.lstrip(".")).lower()
    except Exception as e:
        logger.warning("Could not read image dimensions for %s: %s", path, e)

    return {
        "kind": "image",
        "image_format": image_format,
        "width": width,
        "height": height,
        "size_bytes": size_bytes,
        "mime_type": mime_type,
    }


# ── VLM-powered reconnaissance prompt ──────────────────────────────────────

_RECON_PROMPT = """\
You are a document/image reconnaissance assistant.
Analyze this image and return a concise JSON object with these fields:
- "contains_text": boolean — whether the image contains readable text
- "document_guess": string — what type of document or visual this is (e.g. "receipt", "diagram", "screenshot", "photo", "chart", "form", "handwritten note", etc.), or "unknown"
- "visual_summary": string — 1-2 sentence description of what the image shows
- "key_elements": list of strings — up to 5 notable elements (text snippets, objects, labels)
- "language_detected": string — primary language if text is present, else "none"

Return ONLY the JSON object, no markdown fences, no extra text."""


async def recon_with_vlm(
    path: str,
    *,
    base_url: str,
    api_key: str,
    model_id: str,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Run VLM reconnaissance on a local image file.

    Returns a dict with VLM-derived fields merged in, or error info.
    Never raises — errors are returned as structured results.
    """
    from vlm.provider import describe_image

    resp = await describe_image(
        image_source=path,
        prompt=_RECON_PROMPT,
        base_url=base_url,
        api_key=api_key,
        model_id=model_id,
        timeout=timeout,
        max_tokens=512,
    )

    if not resp.success:
        return {
            "vlm_success": False,
            "vlm_error": resp.content,
        }

    # Try to parse JSON from VLM response
    import json
    try:
        parsed = json.loads(resp.content)
    except json.JSONDecodeError:
        # VLM didn't return valid JSON — use raw text as summary
        return {
            "vlm_success": True,
            "contains_text": None,
            "document_guess": "unknown",
            "visual_summary": resp.content[:500],
            "key_elements": [],
            "language_detected": "unknown",
        }

    return {
        "vlm_success": True,
        "contains_text": parsed.get("contains_text"),
        "document_guess": parsed.get("document_guess", "unknown"),
        "visual_summary": parsed.get("visual_summary", ""),
        "key_elements": parsed.get("key_elements", []),
        "language_detected": parsed.get("language_detected", "unknown"),
    }
