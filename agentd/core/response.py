from typing import Any, Optional


def ok(data: Any, meta: Optional[dict] = None) -> dict:
    """Wrap a successful response in the standard envelope."""
    return {"data": data, "meta": meta}


def ok_list(data: list, total: int, page: int = 1, page_size: int = 20) -> dict:
    """Wrap a list response with pagination meta."""
    return {"data": data, "meta": {"total": total, "page": page, "page_size": page_size}}
