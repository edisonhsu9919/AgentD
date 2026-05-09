"""Shared tool argument normalization for local-model robustness."""

from __future__ import annotations

import os
from typing import Any


NULL_LIKE_STRINGS = {"null", "none", "undefined"}


class ToolArgumentValidationError(ValueError):
    """Raised when a tool argument is clearly not a raw JSON value."""


def normalize_workspace_path_arg(
    value: Any,
    *,
    workspace_dir: str,
    optional_current_dir: bool = True,
) -> str:
    text = normalize_string_arg(
        value,
        field_name="path",
        allow_empty=optional_current_dir,
    )
    if text == "":
        text = "."
    if optional_current_dir and text.lower() in NULL_LIKE_STRINGS:
        text = "."

    if os.path.isabs(text):
        workspace_real = os.path.realpath(workspace_dir)
        path_real = os.path.realpath(text)
        try:
            common = os.path.commonpath([workspace_real, path_real])
        except ValueError:
            return text
        if common == workspace_real:
            rel = os.path.relpath(path_real, workspace_real)
            return "." if rel == "." else rel

    return text


def normalize_string_arg(
    value: Any,
    *,
    field_name: str,
    allow_empty: bool = False,
) -> str:
    if value is None:
        if allow_empty:
            return ""
        raise ToolArgumentValidationError(
            f"TOOL_ARGUMENT_VALIDATION_ERROR: {field_name} is required."
        )
    if not isinstance(value, str):
        raise ToolArgumentValidationError(
            f"TOOL_ARGUMENT_VALIDATION_ERROR: {field_name} must be a JSON string."
        )

    text = value.strip()
    text = _strip_one_outer_quote_layer(text)
    if text == "" and allow_empty:
        return ""
    if text == "":
        raise ToolArgumentValidationError(
            f"TOOL_ARGUMENT_VALIDATION_ERROR: {field_name} cannot be empty."
        )
    if _looks_like_prompt_contamination(text):
        raise ToolArgumentValidationError(
            "TOOL_ARGUMENT_VALIDATION_ERROR: pass raw JSON strings only; "
            f"{field_name} appears to contain prompt or tool-call markup."
        )
    return text


def _strip_one_outer_quote_layer(text: str) -> str:
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1].strip()
    return text


def _looks_like_prompt_contamination(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "```",
        "<tool",
        "</tool",
        "tool_call",
        "tool call",
        "assistant:",
        "user:",
    )
    return "\n" in text or any(marker in lowered for marker in markers)
