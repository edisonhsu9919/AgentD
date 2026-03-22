"""Agent worker — independent process that claims and executes agent_runs.

Usage:
    python -m agent.worker [--worker-id W1] [--poll-interval 2]

Phase C: Each worker is a standalone asyncio process. It polls the
agent_runs table for queued work, claims one run at a time (v1),
executes it via the executor, and marks it complete/failed.

The worker does NOT serve HTTP. It shares the same DB and checkpointer
as the API server.
"""

import argparse
import asyncio
import os
import signal
import traceback
import uuid
from datetime import datetime, timezone

from agent import scheduler
from agent.executor import execute_resume, execute_start
from core.config import settings
from core.database import AsyncSessionLocal


# ── Configuration ────────────────────────────────────────────────────────

DEFAULT_POLL_INTERVAL = 2  # seconds between queue polls
DEFAULT_LEASE_SECONDS = 300  # 5 minutes


# ── Worker ───────────────────────────────────────────────────────────────


class AgentWorker:
    """Single-run-at-a-time agent worker."""

    def __init__(
        self,
        worker_id: str | None = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ):
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self.poll_interval = poll_interval
        self.lease_seconds = lease_seconds
        self._shutdown = False
        self._current_run_id: uuid.UUID | None = None

    async def run(self) -> None:
        """Main worker loop: poll → claim → execute → repeat."""
        print(
            f"[worker:{self.worker_id}] Starting "
            f"(PID={os.getpid()}, poll={self.poll_interval}s, lease={self.lease_seconds}s)"
        )
        print(f"[worker:{self.worker_id}] Claim loop ready — polling for queued runs")

        while not self._shutdown:
            try:
                claimed = await self._poll_and_execute()
                if not claimed:
                    await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                print(f"[worker:{self.worker_id}] Cancelled, shutting down")
                break
            except Exception:
                print(f"[worker:{self.worker_id}] Unexpected error in poll loop:")
                traceback.print_exc()
                await asyncio.sleep(self.poll_interval)

        print(f"[worker:{self.worker_id}] Stopped")

    async def _poll_and_execute(self) -> bool:
        """Try to claim one run and execute it. Returns True if work was done."""
        # Also reclaim expired leases periodically
        async with AsyncSessionLocal() as db:
            reclaimed = await scheduler.reclaim_expired_runs(db)
            if reclaimed > 0:
                await db.commit()
                print(f"[worker:{self.worker_id}] Reclaimed {reclaimed} expired runs")

        # Claim work
        async with AsyncSessionLocal() as db:
            run = await scheduler.claim_run(db, self.worker_id, self.lease_seconds)
            if run is None:
                await db.commit()
                return False

            run_id = run.id
            session_id = str(run.session_id)
            run_type = run.run_type
            payload = run.payload or {}
            self._current_run_id = run_id

            print(f"[worker:{self.worker_id}] Claimed run {run_id} (type={run_type}, session={session_id})")

            # Mark as running
            await scheduler.mark_running(db, run_id)
            await db.commit()

        # Execute
        try:
            if run_type == "start":
                await self._execute_start(session_id, run_id, payload)
            elif run_type == "resume":
                await self._execute_resume(session_id, run_id, payload)
            elif run_type == "abort":
                await self._execute_abort(session_id, run_id)
            else:
                raise ValueError(f"Unknown run_type: {run_type}")

            # Mark completed
            async with AsyncSessionLocal() as db:
                await scheduler.mark_completed(db, run_id)
                await db.commit()

            print(f"[worker:{self.worker_id}] Completed run {run_id}")

        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            print(f"[worker:{self.worker_id}] Failed run {run_id}: {error_msg}")
            if settings.debug:
                traceback.print_exc()

            async with AsyncSessionLocal() as db:
                await scheduler.mark_failed(db, run_id, error_msg)
                await db.commit()

            # Update session status to error
            from agent.executor import _update_db_status
            await _update_db_status(session_id, "error")
            await self._publish(session_id, {"event": "status_change", "status": "error"})
            await self._publish(session_id, {
                "event": "error", "code": "worker_error", "message": error_msg,
            })

        finally:
            self._current_run_id = None

        return True

    async def _execute_start(self, session_id: str, run_id: uuid.UUID, payload: dict) -> None:
        """Execute a 'start' run."""
        from agent.executor import _update_db_status

        # Update session status from queued → running
        await _update_db_status(session_id, "running")
        await self._publish(session_id, {"event": "status_change", "status": "running"})

        # Build abort checker
        sid = uuid.UUID(session_id)
        check_abort = self._make_abort_checker(sid, run_id)

        await execute_start(
            session_id=session_id,
            user_id=payload["user_id"],
            user_root=payload["user_root"],
            session_dir=payload["session_dir"],
            agent_id=payload["agent_id"],
            model_id=payload["model_id"],
            user_message=payload["user_message"],
            publish=self._publish,
            check_abort=check_abort,
        )

        # Renew lease before completion bookkeeping
        async with AsyncSessionLocal() as db:
            await scheduler.renew_lease(db, run_id, self.lease_seconds)
            await db.commit()

    async def _execute_resume(self, session_id: str, run_id: uuid.UUID, payload: dict) -> None:
        """Execute a 'resume' run."""
        from agent.executor import _update_db_status

        await _update_db_status(session_id, "running")
        await self._publish(session_id, {"event": "status_change", "status": "running"})

        decisions = payload.get("decisions", [])
        sid = uuid.UUID(session_id)
        check_abort = self._make_abort_checker(sid, run_id)

        await execute_resume(
            session_id=session_id,
            decisions=decisions,
            publish=self._publish,
            check_abort=check_abort,
        )

    async def _execute_abort(self, session_id: str, run_id: uuid.UUID) -> None:
        """Execute an 'abort' run — cancel active run + pending permissions."""
        sid = uuid.UUID(session_id)

        async with AsyncSessionLocal() as db:
            # Cancel any active (non-abort) run for this session
            active = await scheduler.get_active_run(db, sid)
            if active and active.id != run_id:
                await scheduler.mark_cancelled(db, active.id)

            # Cancel all pending permission requests (#42 — prevent truth conflict)
            from permission import service as perm_svc
            cancelled_perms = await perm_svc.cancel_pending_by_session(db, sid)
            await db.commit()

            if cancelled_perms > 0:
                print(f"[worker:{self.worker_id}] Abort cancelled {cancelled_perms} pending permission(s)")

        from agent.executor import _update_db_status
        await _update_db_status(session_id, "idle")
        await self._publish(session_id, {"event": "status_change", "status": "idle"})

    def _make_abort_checker(self, session_id: uuid.UUID, current_run_id: uuid.UUID):
        """Create a closure that checks for pending abort signals."""
        async def _check() -> bool:
            if self._shutdown:
                return True
            async with AsyncSessionLocal() as db:
                return await scheduler.has_pending_abort(db, session_id)
        return _check

    async def _publish(self, session_id: str, event: dict) -> None:
        """Publish an SSE event via PG NOTIFY (cross-process) + local event_bus fallback."""
        # Primary: cross-process via PG NOTIFY
        try:
            from core.event_bridge import notify
            await notify(session_id, event)
        except Exception as e:
            # Log prominently — silent swallowing here was the root cause of #41
            print(f"[worker:{self.worker_id}] event_bridge.notify FAILED: {type(e).__name__}: {e}")
            if settings.debug:
                traceback.print_exc()

        # Secondary: local event_bus (useful when API and worker share the same process)
        try:
            from core.events import event_bus
            await event_bus.publish(session_id, event)
        except Exception:
            pass

    def shutdown(self) -> None:
        """Signal the worker to stop after the current run completes."""
        self._shutdown = True
        print(f"[worker:{self.worker_id}] Shutdown requested")


# ── Entry point ──────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="AgentD Worker Process")
    parser.add_argument("--worker-id", default=None, help="Unique worker identifier")
    parser.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL)
    parser.add_argument("--lease-seconds", type=int, default=DEFAULT_LEASE_SECONDS)
    args = parser.parse_args()

    worker = AgentWorker(
        worker_id=args.worker_id,
        poll_interval=args.poll_interval,
        lease_seconds=args.lease_seconds,
    )

    loop = asyncio.new_event_loop()

    def _signal_handler(sig, frame):
        worker.shutdown()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        loop.run_until_complete(worker.run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
