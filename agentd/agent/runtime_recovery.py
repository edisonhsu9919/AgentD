"""Failure finalization helpers for session fail-soft runtime."""

from __future__ import annotations

import uuid
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from agent import scheduler
from agent.run_models import AgentRun
from agent.runtime_error_classifier import (
    RecoveryEnvelope,
    RuntimeErrorClassifier,
    session_status_for_envelope,
)
from session import service as session_svc

logger = logging.getLogger(__name__)


async def finalize_run_failure(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    run_id: uuid.UUID,
    exc: BaseException,
    run_type: str | None = None,
    context: dict[str, Any] | None = None,
) -> RecoveryEnvelope:
    """Persist failed run diagnostics and decide session status.

    Phase v0.4.7 / A keeps failed runs as failed, but only terminal envelopes
    are allowed to push the owning session into ``error``.
    """
    error_msg = getattr(exc, "provider_error", None) or f"{type(exc).__name__}: {exc}"
    envelope = RuntimeErrorClassifier.classify_exception(
        exc,
        run_type=run_type,
        context=context,
    )

    await scheduler.mark_failed(db, run_id, error_msg)
    await merge_recovery_envelope(db, run_id, envelope)
    await session_svc.update_session_status(
        db,
        session_id,
        session_status_for_envelope(envelope),
    )
    return envelope


async def persist_session_recovery_envelope(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    envelope: RecoveryEnvelope,
    run_id: uuid.UUID | None = None,
    extra_diagnostics: dict[str, Any] | None = None,
    update_session_status: bool = True,
) -> uuid.UUID | None:
    """Persist recovery truth when no fresh failed run may exist.

    Late bridge failures can happen between parent runs. Phase B still needs
    those envelopes to survive refresh/restart, so we attach them to the
    supplied run when possible, otherwise to the latest run for the session.
    """
    target_run_id = run_id
    if target_run_id is not None and await db.get(AgentRun, target_run_id) is None:
        target_run_id = None

    if target_run_id is None:
        stmt = (
            sa_select(AgentRun)
            .where(AgentRun.session_id == session_id)
            .order_by(AgentRun.updated_at.desc())
            .limit(1)
        )
        latest_run = (await db.execute(stmt)).scalar_one_or_none()
        target_run_id = latest_run.id if latest_run else None

    if target_run_id is not None:
        await merge_recovery_envelope(
            db,
            target_run_id,
            envelope,
            extra_diagnostics={
                "recovery_scope": "session",
                "recovery_unresolved": True,
                **(extra_diagnostics or {}),
            },
        )
    else:
        logger.warning(
            "Recovery envelope could not be persisted: session_id=%s category=%s reason=no_target_run",
            session_id,
            envelope.category,
        )

    if update_session_status:
        await session_svc.update_session_status(
            db,
            session_id,
            session_status_for_envelope(envelope),
        )
    return target_run_id


async def merge_recovery_envelope(
    db: AsyncSession,
    run_id: uuid.UUID,
    envelope: RecoveryEnvelope,
    extra_diagnostics: dict[str, Any] | None = None,
) -> None:
    run = await db.get(AgentRun, run_id)
    raw_diagnostics = getattr(run, "diagnostics", None) if run else None
    diagnostics = dict(raw_diagnostics) if isinstance(raw_diagnostics, dict) else {}
    diagnostics.update(extra_diagnostics or {})
    diagnostics["recovery_envelope"] = envelope.model_dump(mode="json")
    diagnostics["recovery_state"] = envelope.recovery_state
    diagnostics["last_run_error_category"] = envelope.category
    diagnostics["provider_error_category"] = envelope.category
    diagnostics.setdefault("recovery_unresolved", True)
    await scheduler.update_diagnostics(db, run_id, diagnostics)


async def mark_recovery_resolved(
    db: AsyncSession,
    *,
    source_run_id: uuid.UUID,
    resolved_by_run_id: uuid.UUID,
    resolution: str,
) -> None:
    run = await db.get(AgentRun, source_run_id)
    if not run:
        return
    raw_diagnostics = getattr(run, "diagnostics", None)
    diagnostics = dict(raw_diagnostics) if isinstance(raw_diagnostics, dict) else {}
    if not diagnostics.get("recovery_envelope"):
        return
    envelope = dict(diagnostics["recovery_envelope"])
    auto_recovery = envelope.get("auto_recovery")
    auto_recovery = dict(auto_recovery) if isinstance(auto_recovery, dict) else {}
    auto_recovery["resolved_at"] = datetime.now(timezone.utc).isoformat()
    auto_recovery["resolved_by_run_id"] = str(resolved_by_run_id)
    auto_recovery["resolution"] = resolution
    envelope["auto_recovery"] = auto_recovery
    envelope["next_action"] = "none"
    envelope["recovery_resolved"] = True
    diagnostics["recovery_unresolved"] = False
    diagnostics["recovery_state"] = "none"
    diagnostics["recovery_envelope"] = envelope
    diagnostics["resolved_at"] = auto_recovery["resolved_at"]
    diagnostics["resolved_by_run_id"] = str(resolved_by_run_id)
    diagnostics["resolution"] = resolution
    await scheduler.update_diagnostics(db, source_run_id, diagnostics)
