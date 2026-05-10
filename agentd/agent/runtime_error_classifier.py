"""Runtime error taxonomy and recovery envelope for v0.4.7 Phase A."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field


Severity = Literal["recoverable", "terminal", "warning"]
Source = Literal["provider", "tool", "runtime", "checkpoint", "subtask", "compaction", "config"]
NextAction = Literal[
    "none",
    "retry",
    "recover",
    "auto_recovering",
    "continue_with_new_prompt",
    "admin_fix_config",
    "terminal",
]


class RecoveryEnvelope(BaseModel):
    category: str
    severity: Severity
    source: Source
    safe_to_retry: bool
    safe_to_continue_user_prompt: bool
    next_action: NextAction
    user_message: str
    developer_message: str
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    auto_recovery: dict[str, Any] = Field(default_factory=dict)

    @property
    def recovery_state(self) -> str:
        if self.severity == "terminal":
            return "terminal"
        if self.next_action == "auto_recovering":
            return "auto_recovering"
        if self.next_action == "admin_fix_config":
            return "user_action_required"
        if self.severity == "recoverable":
            return "recoverable"
        return "none"


class RuntimeErrorClassifier:
    """Single entry point for mapping runtime failures to RecoveryEnvelope."""

    RECOVERABLE_CATEGORIES = {
        "provider_transient",
        "provider_rate_limit",
        "provider_empty_stream",
        "provider_context_overflow",
        "provider_protocol_reasoning",
        "provider_protocol_tool_adjacency",
        "provider_config_error",
        "provider_bad_request_params",
        "tool_user_error",
        "tool_runtime_error",
        "tool_loop_breaker",
        "hitl_resume_mismatch",
        "subtask_bridge_error",
        "compaction_error",
        # v0.4.9 Phase A: projection mismatches no longer kill the session.
        # The DB tail is now diagnostics-only; classification stays for visibility,
        # but the failure path joins the recoverable family.
        "checkpoint_projection_mismatch",
    }
    TERMINAL_CATEGORIES = {
        "internal_invariant_violation",
        # v0.4.9 Phase A audit Finding 2: real LangGraph checkpoint corruption
        # (state.checkpoint_valid=false / checkpoint_invalid:* reasons) must
        # remain terminal so doctor / admin gets a clear signal. This is
        # distinct from projection mismatches, which are recoverable.
        "checkpoint_corruption",
    }

    @classmethod
    def classify_exception(
        cls,
        exc: BaseException,
        *,
        run_type: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> RecoveryEnvelope:
        merged_context: dict[str, Any] = {
            **(context or {}),
            "exception_type": type(exc).__name__,
            **_exception_hints(exc),
        }
        # v0.4.9 Phase A audit Finding 2: when the exception is a
        # RuntimeIntegrityError, lift the structured decision.reason into
        # context so classification can distinguish recoverable projection
        # mismatches from real checkpoint corruption.
        decision = getattr(exc, "decision", None)
        decision_reason = getattr(decision, "reason", None)
        if decision_reason:
            merged_context.setdefault("integrity_gate_reason", str(decision_reason))
        return cls.classify_error_text(
            f"{type(exc).__name__}: {exc}",
            run_type=run_type,
            context=merged_context,
        )

    @classmethod
    def classify_error_text(
        cls,
        error: str | None,
        *,
        run_type: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> RecoveryEnvelope:
        context = dict(context or {})
        if run_type is not None:
            context.setdefault("run_type", run_type)
        text = error or ""
        category = cls.category_from_text(text, context=context)
        severity = "terminal" if category in cls.TERMINAL_CATEGORIES else "recoverable"
        source = _source_for_category(category)
        next_action = _next_action_for_category(category, severity)
        safe_to_retry = next_action in {"retry", "recover", "auto_recovering"}
        safe_to_continue = severity != "terminal"
        if category in {"provider_config_error", "provider_bad_request_params"}:
            safe_to_retry = False
        if category == "provider_context_overflow":
            safe_to_retry = True

        auto_recovery_allowed = category in {
            "provider_transient",
            "provider_empty_stream",
            "provider_context_overflow",
        }
        auto_recovery = {
            "allowed": auto_recovery_allowed,
            "attempted": int(context.get("auto_recovery_attempted") or 0),
            "max_attempts": 1 if auto_recovery_allowed else 0,
            "next_strategy": (
                "reactive_compact_then_continue"
                if category == "provider_context_overflow"
                else "narrow_continue_retry"
                if category in {"provider_transient", "provider_empty_stream"}
                else None
            ),
            "last_attempt_at": context.get("auto_recovery_last_attempt_at"),
            "last_attempt_error": context.get("auto_recovery_last_attempt_error"),
        }

        return RecoveryEnvelope(
            category=category,
            severity=severity,  # type: ignore[arg-type]
            source=source,  # type: ignore[arg-type]
            safe_to_retry=safe_to_retry,
            safe_to_continue_user_prompt=safe_to_continue,
            next_action=next_action,  # type: ignore[arg-type]
            user_message=_user_message(category),
            developer_message=text,
            diagnostics={
                "run_type": run_type,
                **context,
            },
            auto_recovery=auto_recovery,
        )

    @staticmethod
    def category_from_text(error: str | None, *, context: dict[str, Any] | None = None) -> str:
        text = (error or "").lower()
        context = context or {}
        run_type = str(context.get("run_type") or "").lower()

        explicit = context.get("category") or context.get("provider_error_category")
        mapped = _map_legacy_provider_category(str(explicit)) if explicit else None
        if mapped:
            return mapped
        if run_type == "subtask_bridge":
            return "subtask_bridge_error"

        # v0.4.9 Phase A audit Finding 2: prefer structured RuntimeIntegrityError
        # reason over text matching. checkpoint_invalid:* / checkpoint corruption
        # is terminal; projection mismatches and unrecognized states fall
        # through to recoverable.
        gate_reason = str(context.get("integrity_gate_reason") or "").lower()
        if gate_reason:
            if gate_reason.startswith("checkpoint_invalid:"):
                return "checkpoint_corruption"
            if gate_reason.startswith("db_tail_open_tool_call") or gate_reason in {
                "db_tail_user_inserted_between_tool_group",
            }:
                return "provider_protocol_tool_adjacency"
            if gate_reason in {
                "checkpoint_has_active_next",
                "missing_checkpoint_with_runtime_state",
                "pending_permission_without_open_hitl_checkpoint",
                "pending_permission_without_matching_open_hitl_checkpoint",
                "hitl_open_tool_call_missing_pending_permission",
            } or gate_reason.startswith("unsupported_checkpoint_state"):
                return "checkpoint_projection_mismatch"

        # Same priority for raw text matches: checkpoint_invalid before the
        # generic runtimeintegrityerror catch-all.
        if "checkpoint_invalid:" in text or "checkpoint_valid=false" in text:
            return "checkpoint_corruption"

        if _contains_any(text, ["toollopcircuitbreaker", "toolloopcircuitbreaker", "tool loop breaker"]):
            return "tool_loop_breaker"
        if _contains_any(text, [
            "auto-approved hitl interrupt did not advance",
            "number of human decisions",
            "hanging tool calls",
            "human decisions",
            "hitl resume",
        ]):
            return "hitl_resume_mismatch"
        if _contains_any(text, ["no generations found in stream", "empty stream"]):
            return "provider_empty_stream"
        if _contains_any(text, [
            "context size has been exceeded",
            "context_length_exceeded",
            "maximum context length",
            "context length",
            "input too long",
            "max context tokens",
            "context window",
        ]):
            return "provider_context_overflow"
        if _contains_any(text, ["reasoning_content", "thinking mode", "thinking block", "reasoning item"]):
            return "provider_protocol_reasoning"
        if _contains_any(text, [
            "assistant message with 'tool_calls' must be followed",
            "tool_calls must be followed",
            "orphan tool call",
            "orphan tool message",
            "db_tail_open_tool_call",
            "provider payload validation",
            "provider_payload_validation_error",
            "transcript integrity",
            "transcriptintegrityerror",
        ]):
            return "provider_protocol_tool_adjacency"
        if _contains_any(text, [
            "model not exist",
            "model does not exist",
            "unknown model",
            "model config not found",
            "session.model_id",
        ]):
            return "provider_config_error"
        if _contains_any(text, [
            "unexpected keyword argument",
            "unsupported parameter",
            "invalid parameter",
            "top_k",
            "min_p",
        ]):
            return "provider_bad_request_params"
        if _contains_any(text, ["ratelimit", "rate limit", "429", "速率限制", "高峰时段"]):
            return "provider_rate_limit"
        if _contains_any(text, [
            "apiconnectionerror",
            "connection error",
            "connecterror",
            "connection reset",
            "remoteprotocolerror",
            "peer closed connection",
            "readtimeout",
            "apitimeouterror",
            "request timed out",
            "timeout",
            "server closed the connection",
            "database connection closed",
            "500",
            "server error",
        ]):
            return "provider_transient"
        if _contains_any(text, ["runtimeintegrityerror", "checkpoint_projection_mismatch"]):
            return "checkpoint_projection_mismatch"
        if _contains_any(text, ["subtask bridge", "child result bridge", "parent bridge"]):
            return "subtask_bridge_error"
        if _contains_any(text, ["compact", "compaction", "microcompact"]):
            return "compaction_error"
        if _contains_any(text, ["filenotfounderror", "permission denied", "invalid tool argument", "schema validation"]):
            return "tool_user_error"
        if _contains_any(text, ["tool runtime", "cli service", "process exited", "non-zero exit"]):
            return "tool_runtime_error"
        if _contains_any(text, ["invariant", "assertionerror", "non-terminal session status", "refusing to mark run completed"]):
            return "internal_invariant_violation"
        return "tool_runtime_error" if context.get("tool_name") else "provider_transient"


def recovery_state_from_envelope(envelope: dict[str, Any] | RecoveryEnvelope | None) -> str:
    if envelope is None:
        return "none"
    if isinstance(envelope, RecoveryEnvelope):
        return envelope.recovery_state
    severity = str(envelope.get("severity") or "")
    next_action = str(envelope.get("next_action") or "")
    if severity == "terminal":
        return "terminal"
    if next_action == "auto_recovering":
        return "auto_recovering"
    if next_action == "admin_fix_config":
        return "user_action_required"
    if severity == "recoverable":
        return "recoverable"
    return "none"


def session_status_for_envelope(envelope: RecoveryEnvelope) -> str:
    return "error" if envelope.severity == "terminal" else "idle"


def _contains_any(text: str, needles: list[str]) -> bool:
    return any(needle in text for needle in needles)


def _map_legacy_provider_category(category: str) -> str | None:
    value = category.lower()
    mapping = {
        "provider_timeout": "provider_transient",
        "provider_connection_error": "provider_transient",
        "provider_server_error": "provider_transient",
        "provider_rate_limit": "provider_rate_limit",
        "provider_payload_validation_error": "provider_protocol_tool_adjacency",
        "provider_protocol_error": "provider_protocol_tool_adjacency",
        "transcript_integrity_error": "provider_protocol_tool_adjacency",
        "tool_loop_breaker": "tool_loop_breaker",
        "runtime_projection_repaired": "provider_protocol_tool_adjacency",
    }
    return mapping.get(value)


def _source_for_category(category: str) -> str:
    if category.startswith("provider_"):
        if category in {"provider_config_error", "provider_bad_request_params"}:
            return "config"
        return "provider"
    if category.startswith("tool_"):
        return "tool"
    if category.startswith("checkpoint_"):
        return "checkpoint"
    if category.startswith("subtask_"):
        return "subtask"
    if category.startswith("compaction_"):
        return "compaction"
    return "runtime"


def _next_action_for_category(category: str, severity: str) -> str:
    if severity == "terminal":
        # checkpoint_corruption is terminal but actionable by an administrator
        # (storage/checkpointer repair), not just dead.
        if category == "checkpoint_corruption":
            return "admin_fix_config"
        return "terminal"
    if category in {"provider_transient", "provider_rate_limit", "provider_empty_stream"}:
        return "retry"
    if category == "provider_context_overflow":
        return "recover"
    if category in {"provider_config_error", "provider_bad_request_params"}:
        return "admin_fix_config"
    if category in {"provider_protocol_tool_adjacency", "provider_protocol_reasoning", "hitl_resume_mismatch"}:
        return "recover"
    return "continue_with_new_prompt"


def _user_message(category: str) -> str:
    messages = {
        "provider_transient": "模型服务暂时不可用，可以稍后重试或继续输入新的指令。",
        "provider_rate_limit": "模型服务正在限流，可以稍后重试。",
        "provider_empty_stream": "模型本次没有返回内容，可以重试或继续输入新的指令。",
        "provider_context_overflow": "模型上下文超限，后续可通过压缩上下文后恢复，当前会话仍可继续。",
        "provider_protocol_reasoning": "模型推理协议状态不一致，需要恢复后重试或调整模型配置。",
        "provider_protocol_tool_adjacency": "工具调用上下文需要修复后才能继续重试，当前会话未被关闭。",
        "provider_config_error": "模型配置不可用，请修复模型配置后重试。",
        "provider_bad_request_params": "模型参数不兼容，请修复模型参数配置后重试。",
        "tool_loop_breaker": "工具重复调用已被阻断，可以换一种指令继续。",
        "hitl_resume_mismatch": "人工确认状态不一致，需要重新同步权限状态后继续。",
        "subtask_bridge_error": "子任务结果桥接失败，主会话仍可继续。",
        "compaction_error": "上下文压缩失败，当前会话仍可继续。",
        "checkpoint_projection_mismatch": "运行时记录与数据库展示存在差异，已记录诊断；当前会话仍可继续，可继续输入新指令。",
        "checkpoint_corruption": "运行时检查发现 checkpoint 已损坏，需要管理员介入修复。",
        "internal_invariant_violation": "运行时内部不变量被破坏，暂时不能安全继续。",
    }
    return messages.get(category, "本次运行失败，但会话仍可继续。")


def _exception_hints(exc: BaseException) -> dict[str, Any]:
    hints: dict[str, Any] = {}
    for name in ("tool_name", "canonical_args", "blocked_count", "identical_call_count", "reason"):
        if hasattr(exc, name):
            hints[name] = getattr(exc, name)
    # Some OpenAI/httpx exceptions carry nested provider details only in repr.
    if getattr(exc, "__cause__", None):
        hints["cause"] = f"{type(exc.__cause__).__name__}: {exc.__cause__}"
    if getattr(exc, "__context__", None):
        hints["context"] = f"{type(exc.__context__).__name__}: {exc.__context__}"
    return hints
