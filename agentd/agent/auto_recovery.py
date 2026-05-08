"""Bounded automatic recovery for v0.4.7 Phase C."""

from __future__ import annotations

import uuid
import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from agent import scheduler
from agent.recovery_policy import RecoveryPolicyInput
from agent.recovery_policy import RecoveryDecisionKind, RecoveryPolicy
from agent.run_models import AgentRun
from agent.runtime_error_classifier import RecoveryEnvelope
from agent.runtime_recovery import merge_recovery_envelope
from session import service as session_svc


PublishFn = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass
class AutoRecoveryResult:
    attempted: bool
    enqueued: bool = False
    run_id: uuid.UUID | None = None
    strategy: str | None = None
    reason: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)


AUTO_RETRY_CATEGORIES = {"provider_transient", "provider_empty_stream"}
REACTIVE_COMPACT_CATEGORY = "provider_context_overflow"
REACTIVE_COMPACT_TIMEOUT_SECONDS = float(
    os.getenv("AGENTD_REACTIVE_COMPACT_TIMEOUT_SECONDS", "120")
)


async def attempt_auto_recovery(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    failed_run_id: uuid.UUID,
    envelope: RecoveryEnvelope,
    publish: PublishFn | None = None,
) -> AutoRecoveryResult:
    """Attempt one bounded recovery action for a failed run.

    The failed run remains failed. Recovery creates a new continue run when it
    is safe to resume at the model node.
    """
    auto = dict(envelope.auto_recovery or {})
    if not auto.get("allowed"):
        return AutoRecoveryResult(False, reason="auto_recovery_not_allowed")
    attempted = int(auto.get("attempted") or 0)
    max_attempts = int(auto.get("max_attempts") or 0)
    if attempted >= max_attempts:
        return AutoRecoveryResult(False, reason="attempt_budget_exhausted")

    if envelope.category in AUTO_RETRY_CATEGORIES:
        return await _attempt_narrow_continue_retry(
            db,
            session_id=session_id,
            failed_run_id=failed_run_id,
            envelope=envelope,
            attempted=attempted + 1,
            publish=publish,
        )

    if envelope.category == REACTIVE_COMPACT_CATEGORY:
        return await _attempt_reactive_compact_then_continue(
            db,
            session_id=session_id,
            failed_run_id=failed_run_id,
            envelope=envelope,
            attempted=attempted + 1,
            publish=publish,
        )

    return AutoRecoveryResult(False, reason=f"unsupported_category:{envelope.category}")


async def _attempt_narrow_continue_retry(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    failed_run_id: uuid.UUID,
    envelope: RecoveryEnvelope,
    attempted: int,
    publish: PublishFn | None,
) -> AutoRecoveryResult:
    decision = await _recovery_decision(db, session_id, failed_run_id)
    if decision.kind != RecoveryDecisionKind.CONTINUE_MODEL or not decision.allowed:
        await _record_attempt(
            db,
            failed_run_id,
            envelope,
            attempted=attempted,
            next_action="retry",
            last_attempt_error=decision.reason or "checkpoint_not_continuable",
        )
        return AutoRecoveryResult(
            True,
            reason=decision.reason or "checkpoint_not_continuable",
            diagnostics={"checkpoint_state_kind": decision.checkpoint_state_kind},
        )

    run = await scheduler.enqueue_continue(
        db,
        session_id,
        payload={
            **decision.target_payload,
            "auto_recovery": {
                "category": envelope.category,
                "strategy": "narrow_continue_retry",
                "attempted": attempted,
                "source_run_id": str(failed_run_id),
            },
            "auto_recovery_attempted": attempted,
        },
    )
    await _record_attempt(
        db,
        failed_run_id,
        envelope,
        attempted=attempted,
        next_action="auto_recovering",
    )
    await session_svc.update_session_status(db, session_id, "queued")
    await _publish_auto_recovery(
        publish,
        session_id,
        failed_run_id,
        run.id,
        envelope.category,
        "narrow_continue_retry",
    )
    return AutoRecoveryResult(
        True,
        enqueued=True,
        run_id=run.id,
        strategy="narrow_continue_retry",
        reason="auto_retry_enqueued",
    )


async def _attempt_reactive_compact_then_continue(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    failed_run_id: uuid.UUID,
    envelope: RecoveryEnvelope,
    attempted: int,
    publish: PublishFn | None,
) -> AutoRecoveryResult:
    await _record_attempt(
        db,
        failed_run_id,
        envelope,
        attempted=attempted,
        next_action="auto_recovering",
    )
    try:
        compact_result = await asyncio.wait_for(
            _run_reactive_compact(
                session_id=session_id,
                failed_run_id=failed_run_id,
                publish=publish,
            ),
            timeout=REACTIVE_COMPACT_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        compact_result = {
            "compacted": False,
            "reason": "reactive_compact_timeout",
            "error": f"reactive compact exceeded {REACTIVE_COMPACT_TIMEOUT_SECONDS:g}s",
            "compact_exception_type": "TimeoutError",
        }
    except Exception as exc:
        compact_result = {
            "compacted": False,
            "reason": f"reactive_compact_exception:{type(exc).__name__}",
            "error": str(exc),
            "compact_exception": str(exc),
            "compact_exception_type": type(exc).__name__,
        }
    if not compact_result.get("compacted"):
        reason = str(compact_result.get("reason") or "reactive_compact_failed")
        await _record_attempt(
            db,
            failed_run_id,
            envelope,
            attempted=attempted,
            next_action="recover",
            last_attempt_error=reason,
        )
        return AutoRecoveryResult(
            True,
            reason=reason,
            diagnostics={"compact_result": compact_result},
        )

    decision = await _recovery_decision(
        db,
        session_id,
        failed_run_id,
        recovery_diagnostics={
            "reactive_compact_succeeded": True,
            "compact_result": compact_result,
        },
    )
    if decision.kind != RecoveryDecisionKind.CONTINUE_MODEL or not decision.allowed:
        await _record_attempt(
            db,
            failed_run_id,
            envelope,
            attempted=attempted,
            next_action="recover",
            last_attempt_error=decision.reason or "checkpoint_not_continuable_after_compact",
        )
        return AutoRecoveryResult(
            True,
            reason=decision.reason or "checkpoint_not_continuable_after_compact",
            diagnostics={"compact_result": compact_result},
        )

    run = await scheduler.enqueue_continue(
        db,
        session_id,
        payload={
            **decision.target_payload,
            "auto_recovery": {
                "category": envelope.category,
                "strategy": "reactive_compact_then_continue",
                "attempted": attempted,
                "source_run_id": str(failed_run_id),
            },
            "auto_recovery_attempted": attempted,
        },
    )
    await session_svc.update_session_status(db, session_id, "queued")
    await _publish_auto_recovery(
        publish,
        session_id,
        failed_run_id,
        run.id,
        envelope.category,
        "reactive_compact_then_continue",
    )
    return AutoRecoveryResult(
        True,
        enqueued=True,
        run_id=run.id,
        strategy="reactive_compact_then_continue",
        reason="reactive_compact_continue_enqueued",
        diagnostics={"compact_result": compact_result},
    )


async def _recovery_decision(
    db: AsyncSession,
    session_id: uuid.UUID,
    failed_run_id: uuid.UUID,
    *,
    recovery_diagnostics: dict[str, Any] | None = None,
):
    from agent.checkpoint_state import classify_checkpoint_snapshot
    from agent.runtime import build_agent
    from auth.models import User
    from workspace.manager import get_session_dir

    failed_run = await db.get(AgentRun, failed_run_id)
    session = await session_svc.get_session(db, session_id)
    if not session:
        raise RuntimeError(f"Session {session_id} not found")
    user = await db.get(User, session.user_id)
    user_root = user.workspace if user else ""
    session_dir = get_session_dir(user_root, str(session_id))
    agent = await build_agent(
        session_id=str(session_id),
        user_id=str(session.user_id),
        user_root=user_root,
        session_dir=session_dir,
        agent_id=session.agent_id,
        model_id=session.model_id,
        run_id=f"auto-recovery-{failed_run_id}",
    )
    snapshot = await agent.aget_state({"configurable": {"thread_id": str(session_id)}})
    checkpoint_state = classify_checkpoint_snapshot(snapshot)
    diagnostics = getattr(failed_run, "diagnostics", None)
    if isinstance(diagnostics, dict):
        diagnostics = dict(diagnostics)
    else:
        diagnostics = {}
    if recovery_diagnostics:
        diagnostics.update(recovery_diagnostics)
    return RecoveryPolicy.decide(
        RecoveryPolicyInput(
            session_status=getattr(session, "status", None),
            failed_run_status=getattr(failed_run, "status", None),
            failed_run_error=getattr(failed_run, "error", None),
            diagnostics=diagnostics,
            checkpoint_state=checkpoint_state,
            source_run_id=str(failed_run_id),
        )
    )


async def _run_reactive_compact(
    *,
    session_id: uuid.UUID,
    failed_run_id: uuid.UUID,
    publish: PublishFn | None,
) -> dict[str, Any]:
    from agent.compaction import compact_session as compact
    from agent.runtime import build_agent
    from auth.models import User
    from core.config import settings
    from core.database import AsyncSessionLocal
    from workspace.manager import get_session_dir

    async with AsyncSessionLocal() as db:
        session = await session_svc.get_session(db, session_id)
        if not session:
            return {"compacted": False, "reason": "missing_session"}
        user = await db.get(User, session.user_id)
        user_root = user.workspace if user else settings.workspace_root
        session_dir = get_session_dir(user_root, str(session_id))
        model_id = session.model_id
        agent_id = session.agent_id
        user_id = str(session.user_id)

    agent = await build_agent(
        session_id=str(session_id),
        user_id=user_id,
        user_root=user_root,
        session_dir=session_dir,
        agent_id=agent_id,
        model_id=model_id,
        run_id=f"reactive-compact-{failed_run_id}",
    )
    return await compact(
        agent=agent,
        config={"configurable": {"thread_id": str(session_id)}},
        session_id=str(session_id),
        session_dir=session_dir,
        model_id=model_id,
        publish=publish,
    )


async def _record_attempt(
    db: AsyncSession,
    failed_run_id: uuid.UUID,
    envelope: RecoveryEnvelope,
    *,
    attempted: int,
    next_action: str,
    last_attempt_error: str | None = None,
) -> None:
    auto = dict(envelope.auto_recovery or {})
    auto["attempted"] = attempted
    auto["last_attempt_at"] = datetime.now(timezone.utc).isoformat()
    auto["last_attempt_error"] = last_attempt_error
    updated = envelope.model_copy(update={"auto_recovery": auto, "next_action": next_action})
    await merge_recovery_envelope(db, failed_run_id, updated)


async def _publish_auto_recovery(
    publish: PublishFn | None,
    session_id: uuid.UUID,
    source_run_id: uuid.UUID,
    recovery_run_id: uuid.UUID,
    category: str,
    strategy: str,
) -> None:
    if not publish:
        return
    await publish(str(session_id), {
        "event": "recovery_state_changed",
        "run_id": str(source_run_id),
        "recovery_run_id": str(recovery_run_id),
        "old_state": "recoverable",
        "new_state": "auto_recovering",
        "category": category,
        "next_action": "auto_recovering",
        "strategy": strategy,
    })
    await publish(str(session_id), {
        "event": "auto_recovery_enqueued",
        "source_run_id": str(source_run_id),
        "run_id": str(recovery_run_id),
        "category": category,
        "strategy": strategy,
    })
