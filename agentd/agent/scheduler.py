"""Scheduler service — enqueue / claim / complete / fail / lease operations.

All scheduling state lives in the ``agent_runs`` PostgreSQL table.
Workers use ``claim_run()`` which issues SELECT ... FOR UPDATE SKIP LOCKED
to atomically grab work without double-claiming.

Phase C design: §5, §6 of the Phase C brief.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from agent.run_models import AgentRun

# Default lease duration — worker must finish or renew within this window
DEFAULT_LEASE_SECONDS = 300  # 5 minutes


# ── Enqueue ──────────────────────────────────────────────────────────────


async def enqueue_start(
    db: AsyncSession,
    session_id: uuid.UUID,
    payload: dict,
) -> AgentRun:
    """Enqueue a new 'start' run for a session."""
    run = AgentRun(
        session_id=session_id,
        run_type="start",
        status="queued",
        payload=payload,
    )
    db.add(run)
    await db.flush()
    return run


async def enqueue_resume(
    db: AsyncSession,
    session_id: uuid.UUID,
    decisions: list[dict],
) -> AgentRun:
    """Enqueue a 'resume' run after all permissions are resolved."""
    run = AgentRun(
        session_id=session_id,
        run_type="resume",
        status="queued",
        payload={"decisions": decisions},
    )
    db.add(run)
    await db.flush()
    return run


async def enqueue_continue(
    db: AsyncSession,
    session_id: uuid.UUID,
    payload: dict,
) -> AgentRun:
    """Enqueue a checkpoint continuation without appending a user message."""
    if (
        not isinstance(payload, dict)
        or payload.get("mode") != "retry_model_node"
        or not payload.get("source_run_id")
    ):
        raise ValueError("continue run requires mode=retry_model_node and source_run_id")
    run = AgentRun(
        session_id=session_id,
        run_type="continue",
        status="queued",
        payload=payload,
    )
    db.add(run)
    await db.flush()
    return run


async def enqueue_abort(
    db: AsyncSession,
    session_id: uuid.UUID,
) -> AgentRun:
    """Enqueue an 'abort' signal for a session."""
    run = AgentRun(
        session_id=session_id,
        run_type="abort",
        status="queued",
        payload={},
    )
    db.add(run)
    await db.flush()
    return run


# ── Claim ────────────────────────────────────────────────────────────────


async def claim_run(
    db: AsyncSession,
    worker_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> Optional[AgentRun]:
    """Atomically claim the oldest queued run.

    Uses FOR UPDATE SKIP LOCKED so multiple workers never grab the same row.
    Returns None if no work is available.
    """
    now = datetime.now(timezone.utc)
    lease_until = now + timedelta(seconds=lease_seconds)

    # Subquery: find the oldest queued run
    stmt = (
        select(AgentRun)
        .where(AgentRun.status == "queued")
        .order_by(AgentRun.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    result = await db.execute(stmt)
    run = result.scalar_one_or_none()

    if run is None:
        return None

    run.status = "claimed"
    run.worker_id = worker_id
    run.lease_expires_at = lease_until
    run.updated_at = now
    await db.flush()
    return run


# ── Status transitions ───────────────────────────────────────────────────


async def mark_running(db: AsyncSession, run_id: uuid.UUID) -> None:
    """Transition a claimed run to 'running'."""
    now = datetime.now(timezone.utc)
    await db.execute(
        sql_update(AgentRun)
        .where(AgentRun.id == run_id)
        .values(status="running", updated_at=now)
    )
    await db.flush()


async def mark_completed(db: AsyncSession, run_id: uuid.UUID) -> None:
    """Mark a run as successfully completed."""
    now = datetime.now(timezone.utc)
    await db.execute(
        sql_update(AgentRun)
        .where(AgentRun.id == run_id)
        .values(status="completed", lease_expires_at=None, updated_at=now)
    )
    await db.flush()


async def mark_failed(db: AsyncSession, run_id: uuid.UUID, error: str) -> None:
    """Mark a run as failed with an error message."""
    now = datetime.now(timezone.utc)
    await db.execute(
        sql_update(AgentRun)
        .where(AgentRun.id == run_id)
        .values(status="failed", error=error, lease_expires_at=None, updated_at=now)
    )
    await db.flush()


async def mark_cancelled(db: AsyncSession, run_id: uuid.UUID) -> None:
    """Mark a run as cancelled (e.g. by abort)."""
    now = datetime.now(timezone.utc)
    await db.execute(
        sql_update(AgentRun)
        .where(AgentRun.id == run_id)
        .values(status="cancelled", lease_expires_at=None, updated_at=now)
    )
    await db.flush()


async def update_diagnostics(db: AsyncSession, run_id: uuid.UUID, diagnostics: dict) -> None:
    """Store runtime diagnostics for a run (Phase L: prompt continuity tracking)."""
    now = datetime.now(timezone.utc)
    await db.execute(
        sql_update(AgentRun)
        .where(AgentRun.id == run_id)
        .values(diagnostics=diagnostics, updated_at=now)
    )
    await db.flush()


# ── Lease management ─────────────────────────────────────────────────────


async def renew_lease(
    db: AsyncSession,
    run_id: uuid.UUID,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> None:
    """Extend the lease for a running run (heartbeat)."""
    now = datetime.now(timezone.utc)
    lease_until = now + timedelta(seconds=lease_seconds)
    await db.execute(
        sql_update(AgentRun)
        .where(AgentRun.id == run_id)
        .values(lease_expires_at=lease_until, updated_at=now)
    )
    await db.flush()


async def reclaim_expired_runs(db: AsyncSession) -> int:
    """Reset expired claimed/running runs back to queued.

    Returns the count of runs reclaimed. Called periodically by workers
    or a maintenance task to handle dead worker recovery.
    """
    now = datetime.now(timezone.utc)
    result = await db.execute(
        sql_update(AgentRun)
        .where(
            AgentRun.status.in_(["claimed", "running"]),
            AgentRun.lease_expires_at.isnot(None),
            AgentRun.lease_expires_at < now,
        )
        .values(status="queued", worker_id=None, lease_expires_at=None, updated_at=now)
    )
    await db.flush()
    return result.rowcount


# ── Queries ──────────────────────────────────────────────────────────────


async def has_pending_abort(db: AsyncSession, session_id: uuid.UUID) -> bool:
    """Check if there's a queued abort run for a session.

    Workers call this at execution boundaries to detect abort requests.
    """
    stmt = (
        select(AgentRun.id)
        .where(
            AgentRun.session_id == session_id,
            AgentRun.run_type == "abort",
            AgentRun.status == "queued",
        )
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None


async def get_active_run(db: AsyncSession, session_id: uuid.UUID) -> Optional[AgentRun]:
    """Get the currently active (claimed/running) run for a session."""
    stmt = (
        select(AgentRun)
        .where(
            AgentRun.session_id == session_id,
            AgentRun.status.in_(["claimed", "running"]),
        )
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def cancel_queued_runs(db: AsyncSession, session_id: uuid.UUID) -> int:
    """Cancel all queued (not yet claimed) runs for a session.

    Used by abort to prevent queued start/resume runs from being picked up.
    """
    now = datetime.now(timezone.utc)
    result = await db.execute(
        sql_update(AgentRun)
        .where(
            AgentRun.session_id == session_id,
            AgentRun.status == "queued",
        )
        .values(status="cancelled", updated_at=now)
    )
    await db.flush()
    return result.rowcount


# ── Concurrent claim (Phase 7A) ─────────────────────────────────────────


async def claim_run_concurrent(
    db: AsyncSession,
    worker_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    local_exclude: set[uuid.UUID] | None = None,
) -> Optional[AgentRun]:
    """Claim the oldest queued run with global session-level exclusion.

    Two-layer exclusion to prevent checkpoint write conflicts:
    1. DB layer: skip sessions that already have a claimed/running run
       — prevents cross-worker concurrent execution of the same session
    2. Memory layer (local_exclude): skip sessions active in this worker
       — fast-path to avoid unnecessary DB round-trips

    This is the concurrent-mode replacement for claim_run().
    The original claim_run() is preserved for backward compatibility.
    """
    now = datetime.now(timezone.utc)
    lease_until = now + timedelta(seconds=lease_seconds)

    from session.models import Session

    # Subquery: session_ids that already have an active (claimed/running) run
    active_sessions_subq = (
        select(AgentRun.session_id)
        .where(AgentRun.status.in_(["claimed", "running"]))
        .distinct()
        .scalar_subquery()
    )

    # By joining the Session table, with_for_update() locks BOTH the AgentRun and
    # the Session row. Because we use skip_locked=True, if another worker is
    # currently claiming a run for Session S, it holds the lock on the Session S row,
    # causing this query to entirely skip any queued runs for Session S instead of
    # just skipping the specific AgentRun row. This guarantees cross-worker
    # same-session exclusion.
    stmt = (
        select(AgentRun)
        .join(Session, AgentRun.session_id == Session.id)
        .where(AgentRun.status == "queued")
        .where(AgentRun.session_id.notin_(active_sessions_subq))
        .order_by(AgentRun.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )

    # Memory-layer fast-path exclusion
    if local_exclude:
        stmt = stmt.where(AgentRun.session_id.notin_(local_exclude))

    result = await db.execute(stmt)
    run = result.scalar_one_or_none()

    if run is None:
        return None

    run.status = "claimed"
    run.worker_id = worker_id
    run.lease_expires_at = lease_until
    run.updated_at = now
    await db.flush()
    return run


# ── Session-level interrupt (Phase 7A) ───────────────────────────────────


async def request_interrupt(db: AsyncSession, session_id: uuid.UUID) -> None:
    """Set session-level interrupt flag.

    Running worker checks this at each tool boundary via is_interrupted().
    Replaces the old "enqueue abort run" pattern for cross-worker signalling.
    """
    from session.models import Session

    now = datetime.now(timezone.utc)
    await db.execute(
        sql_update(Session)
        .where(Session.id == session_id)
        .values(interrupt_requested_at=now, updated_at=now)
    )
    await db.flush()


async def clear_interrupt(db: AsyncSession, session_id: uuid.UUID) -> None:
    """Clear session interrupt flag after abort is processed."""
    from session.models import Session

    await db.execute(
        sql_update(Session)
        .where(Session.id == session_id)
        .values(interrupt_requested_at=None)
    )
    await db.flush()


async def is_interrupted(db: AsyncSession, session_id: uuid.UUID) -> bool:
    """Check if session has a pending interrupt.

    Used by the concurrent worker's abort checker at each tool boundary.
    Also falls back to checking queued abort runs for backward compatibility
    with the old single-run worker model.
    """
    from session.models import Session

    # Primary: session-level interrupt flag
    result = await db.execute(
        select(Session.interrupt_requested_at)
        .where(Session.id == session_id)
    )
    row = result.first()
    if row is not None and row[0] is not None:
        return True

    # Fallback: legacy queued abort run (backward compat)
    return await has_pending_abort(db, session_id)
