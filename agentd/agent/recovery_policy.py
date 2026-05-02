"""Recovery policy for narrow checkpoint continuation.

Phase v0.4.4 / Phase B: this module is the business decision layer for
runtime recovery. Diagnostics remain an audit mirror; live checkpoint state
is the source of truth for whether a model continuation is safe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agent.checkpoint_state import CheckpointState, CheckpointStateKind
from agent.diagnostics import ProviderErrorCategory


class RecoveryDecisionKind(str, Enum):
    NONE = "none"
    WAITING_PERMISSION = "waiting_permission"
    RESUME_OPEN_HITL = "resume_open_hitl"
    CONTINUE_MODEL = "continue_model"
    HARD_ERROR = "hard_error"


@dataclass(frozen=True)
class RecoveryPolicyInput:
    session_status: str | None = None
    failed_run_status: str | None = None
    failed_run_error: str | None = None
    diagnostics: dict[str, Any] | None = None
    checkpoint_state: CheckpointState | None = None
    provider_error_category: str | None = None
    hitl_permission_state: str | None = None
    source_run_id: str | None = None


@dataclass(frozen=True)
class RecoveryDecision:
    kind: RecoveryDecisionKind
    allowed: bool = False
    retry_kind: str | None = None
    reason: str = ""
    target_run_type: str | None = None
    target_payload: dict[str, Any] = field(default_factory=dict)
    target_session_status: str | None = None
    provider_error_category: str | None = None
    checkpoint_state_kind: str | None = None


class RecoveryPolicy:
    """Pure recovery decision facade."""

    ACTIVE_STATUSES = {"queued", "running", "waiting", "subtask_waiting"}
    PROVIDER_CONTINUABLE = {
        ProviderErrorCategory.PROVIDER_TIMEOUT.value,
        ProviderErrorCategory.PROVIDER_CONNECTION_ERROR.value,
        "runtime_projection_repaired",
    }
    PROVIDER_HARD_ERRORS = {
        ProviderErrorCategory.PROVIDER_PAYLOAD_VALIDATION_ERROR.value,
        ProviderErrorCategory.PROVIDER_PROTOCOL_ERROR.value,
        ProviderErrorCategory.TRANSCRIPT_INTEGRITY_ERROR.value,
    }
    HITL_RESOLVED_STATES = {"approved", "denied", "resolved", "resumed", "auto_approved"}

    @classmethod
    def decide(cls, payload: RecoveryPolicyInput) -> RecoveryDecision:
        state = payload.checkpoint_state
        state_kind = state.state_kind.value if state else None
        category = cls._resolve_provider_error_category(payload)

        if state and state.state_kind == CheckpointStateKind.HITL_OPEN_TOOL_CALL:
            return cls._decide_hitl(payload, category, state_kind)

        if payload.session_status in cls.ACTIVE_STATUSES:
            return RecoveryDecision(
                kind=RecoveryDecisionKind.NONE,
                reason=f"session_status_active:{payload.session_status}",
                provider_error_category=category,
                checkpoint_state_kind=state_kind,
            )

        if not payload.failed_run_status and not payload.failed_run_error:
            return RecoveryDecision(
                kind=RecoveryDecisionKind.NONE,
                reason="no_failed_run",
                provider_error_category=category,
                checkpoint_state_kind=state_kind,
            )

        if payload.failed_run_status and payload.failed_run_status != "failed":
            return RecoveryDecision(
                kind=RecoveryDecisionKind.NONE,
                reason=f"failed_run_status_not_failed:{payload.failed_run_status}",
                provider_error_category=category,
                checkpoint_state_kind=state_kind,
            )

        if state is None:
            return RecoveryDecision(
                kind=RecoveryDecisionKind.HARD_ERROR,
                reason="missing_live_checkpoint_state",
                provider_error_category=category,
                checkpoint_state_kind=None,
            )

        if not cls._checkpoint_allows_continue(state):
            return RecoveryDecision(
                kind=RecoveryDecisionKind.HARD_ERROR,
                reason=f"checkpoint_not_continuable:{state.state_kind.value}",
                provider_error_category=category,
                checkpoint_state_kind=state_kind,
            )

        if category in cls.PROVIDER_CONTINUABLE:
            if not payload.source_run_id:
                return RecoveryDecision(
                    kind=RecoveryDecisionKind.HARD_ERROR,
                    reason="missing_source_run_id",
                    provider_error_category=category,
                    checkpoint_state_kind=state_kind,
                )
            reason = (
                "db_projection_ahead_repaired_after_closed_tool_result"
                if category == "runtime_projection_repaired"
                else "provider_failure_after_closed_tool_result"
            )
            return RecoveryDecision(
                kind=RecoveryDecisionKind.CONTINUE_MODEL,
                allowed=True,
                retry_kind="model_continuation",
                reason=reason,
                target_run_type="continue",
                target_payload={
                    "mode": "retry_model_node",
                    "source_run_id": payload.source_run_id,
                },
                target_session_status="queued",
                provider_error_category=category,
                checkpoint_state_kind=state_kind,
            )

        if category in cls.PROVIDER_HARD_ERRORS:
            return RecoveryDecision(
                kind=RecoveryDecisionKind.HARD_ERROR,
                reason=f"provider_error_not_continuable:{category}",
                provider_error_category=category,
                checkpoint_state_kind=state_kind,
            )

        return RecoveryDecision(
            kind=RecoveryDecisionKind.HARD_ERROR,
            reason=f"provider_error_unknown_or_not_continuable:{category}",
            provider_error_category=category,
            checkpoint_state_kind=state_kind,
        )

    @classmethod
    def _decide_hitl(
        cls,
        payload: RecoveryPolicyInput,
        category: str | None,
        state_kind: str | None,
    ) -> RecoveryDecision:
        permission_state = payload.hitl_permission_state
        if permission_state == "pending":
            return RecoveryDecision(
                kind=RecoveryDecisionKind.WAITING_PERMISSION,
                allowed=False,
                retry_kind="hitl_resume",
                reason="open_hitl_permission_pending",
                target_session_status="waiting",
                provider_error_category=category,
                checkpoint_state_kind=state_kind,
            )
        if permission_state in cls.HITL_RESOLVED_STATES:
            return RecoveryDecision(
                kind=RecoveryDecisionKind.RESUME_OPEN_HITL,
                allowed=True,
                retry_kind="hitl_resume",
                reason="open_hitl_permission_resolved",
                target_run_type="resume",
                target_session_status="queued",
                provider_error_category=category,
                checkpoint_state_kind=state_kind,
            )
        return RecoveryDecision(
            kind=RecoveryDecisionKind.HARD_ERROR,
            reason=f"open_hitl_permission_not_resolved:{permission_state or 'unknown'}",
            provider_error_category=category,
            checkpoint_state_kind=state_kind,
        )

    @staticmethod
    def _checkpoint_allows_continue(state: CheckpointState) -> bool:
        return (
            state.state_kind == CheckpointStateKind.NEXT_MODEL_AFTER_TOOL_RESULT
            and state.checkpoint_valid
            and state.is_provider_payload_ready
            and state.interrupt_count == 0
        )

    @classmethod
    def _resolve_provider_error_category(
        cls,
        payload: RecoveryPolicyInput,
    ) -> str:
        if payload.provider_error_category:
            return str(payload.provider_error_category)
        diagnostics = payload.diagnostics if isinstance(payload.diagnostics, dict) else {}
        diag_category = diagnostics.get("provider_error_category")
        if diag_category:
            return str(diag_category)
        if diagnostics.get("projection_repair_recoverable"):
            return "runtime_projection_repaired"
        if diagnostics.get("recoverable_model_continuation"):
            return ProviderErrorCategory.PROVIDER_TIMEOUT.value
        return cls._category_from_error_text(payload.failed_run_error)

    @staticmethod
    def _category_from_error_text(error: str | None) -> str:
        text = (error or "").lower()
        if not text:
            return ProviderErrorCategory.UNKNOWN.value
        if "transcriptintegrityerror" in text or "transcript integrity" in text:
            return ProviderErrorCategory.TRANSCRIPT_INTEGRITY_ERROR.value
        if "provider_payload_validation_error" in text or "payload validation" in text:
            return ProviderErrorCategory.PROVIDER_PAYLOAD_VALIDATION_ERROR.value
        if "timeout" in text or "readtimeout" in text:
            return ProviderErrorCategory.PROVIDER_TIMEOUT.value
        if "connection" in text or "connecterror" in text:
            return ProviderErrorCategory.PROVIDER_CONNECTION_ERROR.value
        if "400" in text or "bad request" in text or "protocol" in text:
            return ProviderErrorCategory.PROVIDER_PROTOCOL_ERROR.value
        if "429" in text or "rate limit" in text:
            return ProviderErrorCategory.PROVIDER_RATE_LIMIT.value
        if "401" in text or "403" in text or "auth" in text:
            return ProviderErrorCategory.PROVIDER_AUTH_ERROR.value
        if "500" in text or "server error" in text:
            return ProviderErrorCategory.PROVIDER_SERVER_ERROR.value
        return ProviderErrorCategory.UNKNOWN.value
