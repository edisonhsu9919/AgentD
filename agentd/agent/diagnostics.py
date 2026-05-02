"""Diagnostics helpers for AgentD runtime hardening.

Phase v0.4.4 / Phase A: diagnostics are an audit mirror, not business truth.
This module builds stable checkpoint/exception diagnostics without mutating
runtime state or deciding recovery actions.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any

import httpx

from agent.checkpoint_state import (
    CheckpointStateKind,
    checkpoint_composition,
    classify_checkpoint,
    classify_checkpoint_snapshot,
)
from agent.provider_payload_validator import ProviderPayloadValidationError


class ProviderErrorCategory(str, Enum):
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_CONNECTION_ERROR = "provider_connection_error"
    PROVIDER_PAYLOAD_VALIDATION_ERROR = "provider_payload_validation_error"
    PROVIDER_PROTOCOL_ERROR = "provider_protocol_error"
    PROVIDER_RATE_LIMIT = "provider_rate_limit"
    PROVIDER_AUTH_ERROR = "provider_auth_error"
    PROVIDER_SERVER_ERROR = "provider_server_error"
    TRANSCRIPT_INTEGRITY_ERROR = "transcript_integrity_error"
    TOOL_LOOP_BREAKER = "tool_loop_breaker"
    RUNTIME_ERROR = "runtime_error"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CapturedCheckpoint:
    snapshot: Any | None
    messages: list[Any]
    next_nodes: list[str]
    interrupts: list[Any]
    capture_error: str | None = None


async def capture_checkpoint_snapshot(agent, config: dict) -> CapturedCheckpoint:
    """Best-effort checkpoint capture for success and failure diagnostics."""
    try:
        snapshot = await agent.aget_state(config)
        messages = (snapshot.values or {}).get("messages", []) if snapshot else []
        next_nodes = [str(node) for node in (getattr(snapshot, "next", None) or ())]
        interrupts = list(getattr(snapshot, "interrupts", None) or [])
        return CapturedCheckpoint(
            snapshot=snapshot,
            messages=list(messages or []),
            next_nodes=next_nodes,
            interrupts=interrupts,
            capture_error=None,
        )
    except Exception as exc:
        return CapturedCheckpoint(
            snapshot=None,
            messages=[],
            next_nodes=[],
            interrupts=[],
            capture_error=f"{type(exc).__name__}: {exc}",
        )


def build_checkpoint_diagnostics(
    *,
    messages: list[Any] | None = None,
    snapshot=None,
    next_nodes: list[str] | None = None,
    interrupts: list[Any] | None = None,
    run_type: str | None = None,
    exception: BaseException | None = None,
    diagnostics_capture_error: str | None = None,
) -> dict[str, Any]:
    """Build stable checkpoint diagnostics with v0.4.3 field compatibility."""
    messages = list(messages or [])
    if snapshot is not None:
        state = classify_checkpoint_snapshot(snapshot, run_type=run_type)
        values = getattr(snapshot, "values", {}) or {}
        messages = list(values.get("messages", []) or messages)
    else:
        state = classify_checkpoint(
            messages=messages,
            next_nodes=next_nodes or [],
            interrupts=interrupts or [],
            run_type=run_type,
        )

    category = classify_provider_error(exception).value if exception else None
    diagnostics: dict[str, Any] = {
        "checkpoint_state_kind": state.state_kind.value,
        "checkpoint_state_reason": state.reason,
        "checkpoint_next": state.next_nodes,
        "checkpoint_interrupt_count": state.interrupt_count,
        # Back-compat with the v0.4.3 spelling used by live audits.
        "checkpoint_interrupts_count": state.interrupt_count,
        "checkpoint_message_count": state.message_count,
        "checkpoint_composition": checkpoint_composition(messages),
        "checkpoint_valid": state.checkpoint_valid,
        "checkpoint_bad_indices": state.bad_indices,
        "open_tool_call_ids": state.open_tool_call_ids,
        "closed_tool_call_ids": state.closed_tool_call_ids,
        "orphan_tool_call_ids": state.orphan_tool_call_ids,
        "orphan_tool_message_ids": state.orphan_tool_message_ids,
        "requires_human_input": state.requires_human_input,
        "is_provider_payload_ready": state.is_provider_payload_ready,
        "is_recoverable_checkpoint": state.is_recoverable,
        "diagnostics_capture_error": diagnostics_capture_error,
    }
    if exception is not None:
        diagnostics.update({
            "exception_type": type(exception).__name__,
            "exception_message": str(exception),
            "provider_error_category": category,
        })
        if getattr(exception, "provider_payload_validation_error", False):
            issues = getattr(exception, "issues", []) or []
            diagnostics.update({
                "provider_payload_validation_error": True,
                "provider_family": getattr(exception, "provider_family", None),
                "provider_payload_issue_count": len(issues),
                "provider_payload_issues": issues,
            })
    else:
        diagnostics["provider_error_category"] = None

    # Back-compat for the current router/worker retryable timeout path.
    diagnostics["recoverable_model_continuation"] = (
        state.state_kind == CheckpointStateKind.NEXT_MODEL_AFTER_TOOL_RESULT
        and category == ProviderErrorCategory.PROVIDER_TIMEOUT.value
    )
    if diagnostics["recoverable_model_continuation"]:
        diagnostics["retry_kind"] = "model_continuation"

    return diagnostics


def build_exception_diagnostics(
    exc: BaseException,
    captured: CapturedCheckpoint,
    *,
    run_type: str | None = None,
) -> dict[str, Any]:
    return build_checkpoint_diagnostics(
        messages=captured.messages,
        snapshot=captured.snapshot,
        next_nodes=captured.next_nodes,
        interrupts=captured.interrupts,
        run_type=run_type,
        exception=exc,
        diagnostics_capture_error=captured.capture_error,
    )


def classify_provider_error(exc: BaseException | None) -> ProviderErrorCategory:
    if exc is None:
        return ProviderErrorCategory.UNKNOWN

    if isinstance(exc, ProviderPayloadValidationError):
        return ProviderErrorCategory.PROVIDER_PAYLOAD_VALIDATION_ERROR

    try:
        from tools.registry import ToolLoopCircuitBreaker

        if isinstance(exc, ToolLoopCircuitBreaker):
            return ProviderErrorCategory.TOOL_LOOP_BREAKER
    except Exception:
        pass

    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (httpx.TimeoutException, asyncio.TimeoutError, TimeoutError)):
            return ProviderErrorCategory.PROVIDER_TIMEOUT
        if isinstance(current, httpx.ConnectError):
            return ProviderErrorCategory.PROVIDER_CONNECTION_ERROR
        if isinstance(current, httpx.HTTPStatusError):
            status_code = current.response.status_code
            if status_code in {401, 403}:
                return ProviderErrorCategory.PROVIDER_AUTH_ERROR
            if status_code == 429:
                return ProviderErrorCategory.PROVIDER_RATE_LIMIT
            if 400 <= status_code < 500:
                return ProviderErrorCategory.PROVIDER_PROTOCOL_ERROR
            if status_code >= 500:
                return ProviderErrorCategory.PROVIDER_SERVER_ERROR

        name = type(current).__name__.lower()
        message = str(current).lower()
        if "timeout" in name:
            return ProviderErrorCategory.PROVIDER_TIMEOUT
        if "connection" in name or "connect" in name:
            return ProviderErrorCategory.PROVIDER_CONNECTION_ERROR
        if "rate" in message and "limit" in message:
            return ProviderErrorCategory.PROVIDER_RATE_LIMIT
        if "401" in message or "403" in message or "auth" in message:
            return ProviderErrorCategory.PROVIDER_AUTH_ERROR
        if "400" in message or "bad request" in message:
            return ProviderErrorCategory.PROVIDER_PROTOCOL_ERROR
        if "500" in message or "server error" in message:
            return ProviderErrorCategory.PROVIDER_SERVER_ERROR

        current = current.__cause__ or current.__context__

    return ProviderErrorCategory.RUNTIME_ERROR


class DiagnosticsRecorder:
    """Namespaced facade for Phase A diagnostics helpers.

    This class intentionally carries no mutable state. It exists so later
    phases can replace the facade with a fuller recorder without changing
    call sites again.
    """

    capture_checkpoint_snapshot = staticmethod(capture_checkpoint_snapshot)
    build_checkpoint_diagnostics = staticmethod(build_checkpoint_diagnostics)
    build_exception_diagnostics = staticmethod(build_exception_diagnostics)
    classify_provider_error = staticmethod(classify_provider_error)
