"""EML extraction layer (Phase O2).

Pure data extraction from EML files using stdlib email.
No LLM calls — structured raw data only.
"""

import email
import email.policy
import os
from typing import Any

# Limits
_BODY_MAX_CHARS = 2000
_ATTACHMENTS_MAX = 20


def extract(path: str) -> dict[str, Any]:
    """Extract structural metadata and body preview from an EML file.

    Returns a dict with:
      - path, kind, email_kind, size_bytes
      - subject, from_addr, to_addr, date
      - body_preview (plain text, truncated)
      - attachment_count, attachments (list of filename/content_type)

    Raises FileNotFoundError if path doesn't exist.
    Raises ValueError if file is not a valid EML.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {path}")

    size_bytes = os.path.getsize(path)

    try:
        with open(path, "rb") as f:
            msg = email.message_from_binary_file(f, policy=email.policy.default)
    except Exception as e:
        raise ValueError(f"Cannot read EML: {e}") from e

    # Headers
    subject = str(msg.get("Subject", ""))
    from_addr = str(msg.get("From", ""))
    to_addr = str(msg.get("To", ""))
    date = str(msg.get("Date", ""))

    # Body — prefer plain text
    body_preview = _extract_body(msg)

    # Attachments
    attachments: list[dict[str, str]] = []
    for part in msg.walk():
        disposition = part.get_content_disposition()
        if disposition == "attachment":
            filename = part.get_filename() or "(unnamed)"
            content_type = part.get_content_type()
            attachments.append({
                "filename": filename,
                "content_type": content_type,
            })

    return {
        "path": path,
        "kind": "email",
        "email_kind": "eml",
        "size_bytes": size_bytes,
        "subject": subject,
        "from_addr": from_addr,
        "to_addr": to_addr,
        "date": date,
        "body_preview": body_preview,
        "attachment_count": len(attachments),
        "attachments": attachments[:_ATTACHMENTS_MAX],
    }


def _extract_body(msg) -> str:
    """Extract plain text body from email message, capped."""
    body = msg.get_body(preferencelist=("plain", "html"))
    if body is None:
        return ""

    content = body.get_content()
    if not isinstance(content, str):
        return ""

    text = content.strip()
    return text[:_BODY_MAX_CHARS]
