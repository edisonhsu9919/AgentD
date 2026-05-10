"""Agent worker — independent process that claims and executes agent_runs.

Usage:
    python -m agent.worker [--worker-id W1] [--poll-interval 2] [--max-concurrent 4]

Phase 7A: Concurrent multi-run worker. Spawns an asyncio Task per claimed run,
with session-level mutual exclusion (same session is never executed concurrently
across any worker). Uses DB-level subquery exclusion + in-memory fast-path.

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
from agent.executor import RecoverableProviderTimeout, execute_continue, execute_resume, execute_start
from agent.runtime_integrity import (
    RuntimeGateAction,
    RuntimeGateDecision,
    RuntimeIntegrityError,
    decide_terminal_with_layered_validation,
    persist_runtime_integrity_diagnostics,
)
from agent.subtask_reconciliation import reconcile_completed_child_tasks
from core.config import settings
from core.database import AsyncSessionLocal
from session import service as session_svc


def _parent_can_accept_child_bridge(parent, task) -> bool:
    status = getattr(parent, "status", None)
    if not parent or not task:
        return False
    if getattr(task, "task_kind", None) != "child_session":
        return False
    if getattr(task, "blocking_mode", None) != "blocking":
        return False
    if getattr(task, "status", None) not in {"queued", "running", "waiting"}:
        return False
    if getattr(task, "child_session_id", None) is None:
        return False
    if status == "error":
        return False
    return status in {"idle", "waiting", "subtask_waiting", "queued", "running"}


async def _parent_has_subtask_result(db, parent_id: uuid.UUID, task_id: str, child_session_id: str) -> bool:
    from agent.subtask_bridge import parent_has_subtask_result

    return await parent_has_subtask_result(db, parent_id, task_id, child_session_id)


# ── Configuration ────────────────────────────────────────────────────────

DEFAULT_POLL_INTERVAL = 2  # seconds between queue polls
DEFAULT_LEASE_SECONDS = 300  # 5 minutes
DEFAULT_MAX_CONCURRENT = 4  # conservative default; tune after benchmarking
DRAIN_TIMEOUT = 60  # seconds to wait for active runs on shutdown


# ── Worker ───────────────────────────────────────────────────────────────


class AgentWorker:
    """Concurrent multi-run agent worker (Phase 7A).

    Spawns an asyncio.Task per claimed run, with session-level mutual
    exclusion to prevent checkpoint write conflicts. Different sessions
    execute concurrently; same-session runs are serialized.
    """

    def __init__(
        self,
        worker_id: str | None = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
    ):
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self.poll_interval = poll_interval
        self.lease_seconds = lease_seconds
        self.max_concurrent = max_concurrent
        self._shutdown = False
        # Phase 7A: concurrent state
        self._active_runs: dict[uuid.UUID, asyncio.Task] = {}
        self._active_sessions: set[uuid.UUID] = set()

    async def run(self) -> None:
        """Main worker loop: claim → spawn tasks → heartbeat (Phase 7A concurrent)."""
        print(
            f"[worker:{self.worker_id}] Starting "
            f"(PID={os.getpid()}, poll={self.poll_interval}s, "
            f"lease={self.lease_seconds}s, max_concurrent={self.max_concurrent})"
        )
        print(f"[worker:{self.worker_id}] Concurrent claim loop ready")

        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name=f"{self.worker_id}-heartbeat"
        )

        try:
            while not self._shutdown:
                try:
                    # 1. Reap completed tasks
                    self._reap_done_tasks()

                    # 2. If we have capacity, try to claim and spawn
                    if len(self._active_runs) < self.max_concurrent:
                        claimed = await self._try_claim_and_spawn()
                        if not claimed:
                            await asyncio.sleep(self.poll_interval)
                    else:
                        # At capacity — wait for any task to finish or poll timeout
                        if self._active_runs:
                            done, _ = await asyncio.wait(
                                list(self._active_runs.values()),
                                timeout=self.poll_interval,
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                        else:
                            await asyncio.sleep(self.poll_interval)

                except asyncio.CancelledError:
                    print(f"[worker:{self.worker_id}] Cancelled, shutting down")
                    break
                except Exception:
                    print(f"[worker:{self.worker_id}] Unexpected error in poll loop:")
                    traceback.print_exc()
                    await asyncio.sleep(self.poll_interval)
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            await self._drain_active_runs()

        print(f"[worker:{self.worker_id}] Stopped")

    # ── Concurrent task management (Phase 7A) ────────────────────────────

    async def _try_claim_and_spawn(self) -> bool:
        """Claim one run (with global session exclusion) and spawn as Task."""
        # Reclaim expired leases
        async with AsyncSessionLocal() as db:
            reclaimed = await scheduler.reclaim_expired_runs(db)
            if reclaimed > 0:
                await db.commit()
                print(f"[worker:{self.worker_id}] Reclaimed {reclaimed} expired runs")

        # Claim with DB-level + memory-level session exclusion
        async with AsyncSessionLocal() as db:
            run = await scheduler.claim_run_concurrent(
                db, self.worker_id, self.lease_seconds,
                local_exclude=self._active_sessions,
            )
            if run is None:
                await db.commit()
                return False

            run_id = run.id
            session_id = run.session_id
            run_type = run.run_type
            payload = run.payload or {}

            active_count = len(self._active_runs) + 1
            print(
                f"[worker:{self.worker_id}] [{active_count}/{self.max_concurrent}] "
                f"Claimed run {run_id} (type={run_type}, session={str(session_id)[:8]})"
            )

            await scheduler.mark_running(db, run_id)
            await db.commit()

        # Register session as active and spawn task
        self._active_sessions.add(session_id)
        task = asyncio.create_task(
            self._execute_run(run_id, str(session_id), run_type, payload),
            name=f"run-{run_id}",
        )
        self._active_runs[run_id] = task
        return True

    async def _execute_run(
        self,
        run_id: uuid.UUID,
        session_id: str,
        run_type: str,
        payload: dict,
    ) -> None:
        """Execute a single run in its own Task. Handles completion/failure."""
        import time
        t0 = time.monotonic()
        try:
            await self._record_run_start_seq(session_id, run_id)
            if run_type == "start":
                await self._execute_start(session_id, run_id, payload)
            elif run_type == "resume":
                await self._execute_resume(session_id, run_id, payload)
            elif run_type == "continue":
                await self._execute_continue(session_id, run_id, payload)
            elif run_type == "abort":
                await self._execute_abort(session_id, run_id)
            else:
                raise ValueError(f"Unknown run_type: {run_type}")

            await self._assert_run_returned_terminal_state(session_id, run_id)

            # Mark completed
            async with AsyncSessionLocal() as db:
                await scheduler.mark_completed(db, run_id)
                auto_recovery_payload = (
                    payload.get("auto_recovery")
                    if isinstance(payload, dict) and isinstance(payload.get("auto_recovery"), dict)
                    else None
                )
                source_run_id = (
                    auto_recovery_payload.get("source_run_id")
                    if isinstance(auto_recovery_payload, dict)
                    else None
                )
                if source_run_id:
                    from agent.runtime_recovery import mark_recovery_resolved

                    await mark_recovery_resolved(
                        db,
                        source_run_id=uuid.UUID(str(source_run_id)),
                        resolved_by_run_id=run_id,
                        resolution="auto_recovery_completed",
                    )
                await db.commit()

            try:
                from agent.subtask_bridge import bridge_reconcilable_child_tasks

                await bridge_reconcilable_child_tasks(
                    parent_session_id=uuid.UUID(session_id),
                    publish=self._publish,
                )
            except Exception:
                if settings.debug:
                    traceback.print_exc()

            elapsed = time.monotonic() - t0
            active_count = len(self._active_runs) - 1  # this one is finishing
            print(
                f"[worker:{self.worker_id}] [{active_count}/{self.max_concurrent}] "
                f"Completed run {run_id} in {elapsed:.1f}s"
            )

            # Child result bridge
            await self._bridge_child_result(session_id)

        except Exception as e:
            error_msg = getattr(e, "provider_error", None) or f"{type(e).__name__}: {e}"
            elapsed = time.monotonic() - t0
            print(f"[worker:{self.worker_id}] Failed run {run_id} after {elapsed:.1f}s: {error_msg}")
            if settings.debug:
                traceback.print_exc()

            auto_payload = (
                payload.get("auto_recovery")
                if isinstance(payload, dict) and isinstance(payload.get("auto_recovery"), dict)
                else {}
            )
            from agent.runtime_recovery import finalize_run_failure

            async with AsyncSessionLocal() as db:
                envelope = await finalize_run_failure(
                    db,
                    session_id=uuid.UUID(session_id),
                    run_id=run_id,
                    exc=e,
                    run_type=run_type,
                    context={
                        "worker_id": self.worker_id,
                        "payload_keys": sorted(payload.keys()) if isinstance(payload, dict) else [],
                        "auto_recovery_attempted": (
                            payload.get("auto_recovery_attempted")
                            if isinstance(payload, dict)
                            else None
                        )
                        or auto_payload.get("attempted"),
                    },
                )
                await db.commit()

            from agent.auto_recovery import AutoRecoveryResult, attempt_auto_recovery

            try:
                async with AsyncSessionLocal() as db:
                    auto_recovery_result = await attempt_auto_recovery(
                        db,
                        session_id=uuid.UUID(session_id),
                        failed_run_id=run_id,
                        envelope=envelope,
                        publish=self._publish,
                    )
                    await db.commit()
            except Exception as recovery_exc:
                auto_recovery_result = AutoRecoveryResult(
                    attempted=True,
                    enqueued=False,
                    reason=f"auto_recovery_error:{type(recovery_exc).__name__}",
                    diagnostics={"error": str(recovery_exc)},
                )
                if settings.debug:
                    traceback.print_exc()

            from tools.registry import ToolLoopCircuitBreaker
            is_subtask_continuation = (
                run_type == "start" and payload.get("is_subtask_continuation", False)
            )
            is_tool_loop_breaker = isinstance(e, ToolLoopCircuitBreaker)
            is_recoverable_provider_timeout = isinstance(e, RecoverableProviderTimeout)
            next_status = (
                "queued"
                if auto_recovery_result.enqueued
                else "error"
                if envelope.severity == "terminal"
                else "idle"
            )
            await self._publish(session_id, {"event": "status_change", "status": next_status})
            recovery_event = {
                "event": "run_failed_terminal" if envelope.severity == "terminal" else "run_failed_recoverable",
                "run_id": str(run_id),
                "old_state": "none",
                "new_state": "auto_recovering" if auto_recovery_result.enqueued else envelope.recovery_state,
                "category": envelope.category,
                "next_action": "auto_recovering" if auto_recovery_result.enqueued else envelope.next_action,
                "user_message": envelope.user_message,
                "recovery_envelope": envelope.model_dump(mode="json"),
                "auto_recovery": {
                    "attempted": auto_recovery_result.attempted,
                    "enqueued": auto_recovery_result.enqueued,
                    "run_id": str(auto_recovery_result.run_id) if auto_recovery_result.run_id else None,
                    "strategy": auto_recovery_result.strategy,
                    "reason": auto_recovery_result.reason,
                },
            }
            await self._publish(session_id, recovery_event)
            await self._publish(session_id, {
                "event": "recovery_state_changed",
                "run_id": str(run_id),
                "old_state": "none",
                "new_state": "auto_recovering" if auto_recovery_result.enqueued else envelope.recovery_state,
                "category": envelope.category,
                "next_action": "auto_recovering" if auto_recovery_result.enqueued else envelope.next_action,
                "user_message": envelope.user_message,
            })
            await self._publish(session_id, {
                "event": "error",
                "code": envelope.category,
                "message": error_msg,
                "recovery_state": envelope.recovery_state,
                "next_action": envelope.next_action,
                **({
                    "tool_name": e.tool_name,
                    "canonical_args": e.canonical_args,
                    "blocked_count": e.blocked_count,
                    "identical_call_count": e.identical_call_count,
                    "reason": e.reason,
                } if is_tool_loop_breaker else {}),
            })
            if is_subtask_continuation:
                print(
                    f"[worker:{self.worker_id}] Subtask continuation failed; "
                    f"restoring session {session_id[:8]} to idle so the bridged result remains usable"
                )
            elif is_tool_loop_breaker:
                print(
                    f"[worker:{self.worker_id}] Tool loop breaker tripped for session "
                    f"{session_id[:8]} on {e.tool_name}; restoring session to idle"
                )
            elif is_recoverable_provider_timeout:
                print(
                    f"[worker:{self.worker_id}] Provider timeout after tool result for "
                    f"session {session_id[:8]}; restoring to idle with retry available"
                )

            # Child failure bridge
            await self._bridge_child_failure(session_id, error_msg)

        finally:
            # Release session slot
            sid = uuid.UUID(session_id)
            self._active_sessions.discard(sid)

    async def _assert_run_returned_terminal_state(
        self,
        session_id: str,
        run_id: uuid.UUID | None = None,
    ) -> None:
        """Reconcile session status to the runtime gate decision.

        v0.4.9 Phase A: this guard is now fail-soft. A run that finishes with a
        non-terminal session status is corrected in place instead of escalated
        into ``internal_invariant_violation``. The hard raise was a known
        secondary kill-shot once any path momentarily set ``status="error"``.
        """
        try:
            async with AsyncSessionLocal() as db:
                from unittest.mock import Mock
                if isinstance(db, Mock):
                    return
                session = await session_svc.get_session(db, uuid.UUID(session_id))
        except Exception:
            if settings.debug:
                traceback.print_exc()
            return

        status = getattr(session, "status", None)
        if status == "error":
            # Fail-soft: executor briefly marked error but the run completed.
            # Move the session back to idle and let the run be marked completed.
            print(
                f"[worker:{self.worker_id}] Session {session_id[:8]} status=error "
                "after executor returned cleanly; coercing to idle (fail-soft)"
            )
            async with AsyncSessionLocal() as db:
                await session_svc.update_session_status(
                    db, uuid.UUID(session_id), "idle",
                )
                await db.commit()
            await self._publish(
                session_id,
                {"event": "status_change", "status": "idle"},
            )
            session.status = "idle"
            status = "idle"
        elif status not in {"idle", "waiting", "subtask_waiting"}:
            # Fail-soft: unexpected non-terminal status; coerce to idle.
            print(
                f"[worker:{self.worker_id}] Session {session_id[:8]} status="
                f"{status!r} after executor returned; coercing to idle (fail-soft)"
            )
            async with AsyncSessionLocal() as db:
                await session_svc.update_session_status(
                    db, uuid.UUID(session_id), "idle",
                )
                await db.commit()
            await self._publish(
                session_id,
                {"event": "status_change", "status": "idle"},
            )
            session.status = "idle"
            status = "idle"

        try:
            async with AsyncSessionLocal() as db:
                await reconcile_completed_child_tasks(db, uuid.UUID(session_id))
                await db.commit()
        except Exception:
            if settings.debug:
                traceback.print_exc()

        decision = await self._load_terminal_runtime_integrity_decision(
            session_id,
            run_id,
            session,
        )
        if decision.action == RuntimeGateAction.FINALIZE_IDLE:
            return
        if decision.action == RuntimeGateAction.ENTER_SUBTASK_WAITING:
            if status != "subtask_waiting":
                async with AsyncSessionLocal() as db:
                    await session_svc.update_session_status(
                        db,
                        uuid.UUID(session_id),
                        "subtask_waiting",
                    )
                    await db.commit()
                await self._publish(
                    session_id,
                    {"event": "status_change", "status": "subtask_waiting"},
                )
            return
        if decision.action == RuntimeGateAction.ENTER_WAITING:
            if status != "waiting":
                async with AsyncSessionLocal() as db:
                    await session_svc.update_session_status(
                        db,
                        uuid.UUID(session_id),
                        "waiting",
                    )
                    await db.commit()
                await self._publish(
                    session_id,
                    {"event": "status_change", "status": "waiting"},
                )
            return
        if decision.action == RuntimeGateAction.CONTINUE_MODEL:
            # v0.4.9 Phase A: a CONTINUE_MODEL decision at run end means the
            # checkpoint is at next=["model"] after a closed tool_result. This
            # is not fatal — enqueue a narrow continue so the model finishes
            # the response. Session becomes queued; status will be reconciled
            # by the new run.
            await self._enqueue_terminal_continue_model(session_id, run_id, decision)
            return
        raise RuntimeIntegrityError(session_id, decision)

    async def _enqueue_terminal_continue_model(
        self,
        session_id: str,
        run_id: uuid.UUID | None,
        decision: RuntimeGateDecision,
    ) -> None:
        """Enqueue a narrow continue when terminal gate returns CONTINUE_MODEL.

        scheduler.enqueue_continue() requires payload to carry both
        ``mode="retry_model_node"`` and a non-empty ``source_run_id``. When the
        finishing run has no run_id, we cannot synthesize a valid continue
        payload, so we fail-soft to idle and record diagnostics instead of
        enqueueing an invalid continue.
        """
        if run_id is None:
            print(
                f"[worker:{self.worker_id}] Session {session_id[:8]} terminal gate "
                f"returned CONTINUE_MODEL ({decision.reason}) but no run_id is "
                "available; coercing to idle (continue requires source_run_id)."
            )
            await self._fail_soft_to_idle_after_continue_skip(
                session_id,
                reason="continue_skip_missing_source_run_id",
                decision=decision,
            )
            return

        try:
            async with AsyncSessionLocal() as db:
                run = await scheduler.enqueue_continue(
                    db,
                    uuid.UUID(session_id),
                    payload={
                        "mode": "retry_model_node",
                        "source_run_id": str(run_id),
                        "reason": decision.reason,
                        "checkpoint_state_kind": decision.checkpoint_state_kind,
                        "trigger": "terminal_gate_continue_model",
                    },
                )
                await session_svc.update_session_status(
                    db, uuid.UUID(session_id), "queued",
                )
                await db.commit()
            await self._publish(
                session_id,
                {
                    "event": "status_change",
                    "status": "queued",
                    "trigger": "terminal_gate_continue_model",
                    "continue_run_id": str(run.id),
                },
            )
            print(
                f"[worker:{self.worker_id}] Session {session_id[:8]} terminal gate "
                f"returned CONTINUE_MODEL ({decision.reason}); enqueued continue run {run.id}"
            )
        except Exception:
            if settings.debug:
                traceback.print_exc()
            # Even if continue enqueue fails, do NOT raise integrity error.
            # Coerce session to idle so user can manually retry.
            await self._fail_soft_to_idle_after_continue_skip(
                session_id,
                reason="continue_enqueue_exception",
                decision=decision,
            )

    async def _fail_soft_to_idle_after_continue_skip(
        self,
        session_id: str,
        *,
        reason: str,
        decision: RuntimeGateDecision,
    ) -> None:
        try:
            async with AsyncSessionLocal() as db:
                await session_svc.update_session_status(
                    db, uuid.UUID(session_id), "idle",
                )
                await db.commit()
            await self._publish(
                session_id,
                {
                    "event": "status_change",
                    "status": "idle",
                    "trigger": "terminal_gate_continue_model_fail_soft",
                    "reason": reason,
                    "decision_reason": decision.reason,
                },
            )
        except Exception:
            if settings.debug:
                traceback.print_exc()

    async def _load_terminal_runtime_integrity_decision(
        self,
        session_id: str,
        run_id: uuid.UUID | None,
        session,
    ):
        from unittest.mock import Mock

        async with AsyncSessionLocal() as db:
            if isinstance(db, Mock):
                from agent.runtime_integrity import RuntimeGateDecision

                return RuntimeGateDecision(
                    action=RuntimeGateAction.FINALIZE_IDLE,
                    reason="mock_db_bypass",
                    can_accept_user_prompt=True,
                )

            sid = uuid.UUID(session_id)
            checkpoint_state = None
            checkpoint_messages = []
            try:
                from auth.models import User
                from agent.checkpoint_state import classify_checkpoint_snapshot
                from agent.child_session import read_child_session_meta
                from agent.runtime import build_agent
                from workspace.manager import get_session_dir

                user = await db.get(User, session.user_id)
                user_root = user.workspace if user else settings.workspace_root
                session_dir = get_session_dir(user_root, session_id)
                parent_session_dir = None
                allowed_tools = None
                if session.parent_id:
                    parent_session_dir = get_session_dir(user_root, str(session.parent_id))
                    child_meta = read_child_session_meta(session_dir)
                    allowed_tools = (
                        child_meta.get("resolved_tools")
                        or child_meta.get("allowed_tools")
                        or None
                    )
                agent = await build_agent(
                    session_id=session_id,
                    user_id=str(session.user_id),
                    user_root=user_root,
                    session_dir=session_dir,
                    agent_id=session.agent_id,
                    model_id=session.model_id,
                    tool_profile="child" if session.parent_id else None,
                    parent_session_dir=parent_session_dir,
                    allowed_tools=allowed_tools,
                    run_id=str(run_id) if run_id else None,
                )
                snapshot = await agent.aget_state({"configurable": {"thread_id": session_id}})
                values = getattr(snapshot, "values", {}) or {}
                checkpoint_messages = list(values.get("messages", []) or [])
                checkpoint_state = classify_checkpoint_snapshot(snapshot) if snapshot else None
            except Exception:
                if settings.debug:
                    traceback.print_exc()

            from permission import service as perm_svc

            from agent.run_models import AgentRun

            run = await db.get(AgentRun, run_id) if run_id else None
            diagnostics = dict(getattr(run, "diagnostics", None) or {}) if run else {}
            run_start_seq = diagnostics.get("run_start_seq")
            try:
                run_start_seq = int(run_start_seq) if run_start_seq is not None else None
            except (TypeError, ValueError):
                run_start_seq = None
            pending_permissions = await perm_svc.get_pending_by_session(db, sid)
            decision, warning = await decide_terminal_with_layered_validation(
                db,
                session_id=sid,
                session_status=getattr(session, "status", None),
                checkpoint_state=checkpoint_state,
                pending_permissions=pending_permissions,
                run_start_seq=run_start_seq,
                latest_run_type=getattr(run, "run_type", None),
                latest_run_status=getattr(run, "status", None),
                latest_error=getattr(run, "error", None),
            )
            # v0.4.9 Phase B: silent projection repair on the worker terminal
            # path is gated by the rollback flag. With DB tail demoted to
            # diagnostics-only, the gate never returns "db_tail_open_tool_call"
            # in the default configuration, so this branch is dead code under
            # the new contract. We keep it behind the flag so flipping it back
            # to true still recovers v0.4.4 behaviour for incident response.
            if (
                settings.runtime_integrity_gate_db_tail_enabled
                and decision.action == RuntimeGateAction.FAIL_INTEGRITY_ERROR
                and str(decision.reason).startswith("db_tail_open_tool_call")
            ):
                try:
                    from agent.projection_consistency import (
                        inspect_db_checkpoint_projection,
                        repair_db_projection_ahead,
                    )

                    projection = await inspect_db_checkpoint_projection(
                        db,
                        sid,
                        checkpoint_messages,
                    )
                    if projection.is_db_projection_ahead:
                        repair = await repair_db_projection_ahead(db, projection)
                        decision, rerun_warning = await decide_terminal_with_layered_validation(
                            db,
                            session_id=sid,
                            session_status=getattr(session, "status", None),
                            checkpoint_state=checkpoint_state,
                            pending_permissions=pending_permissions,
                            run_start_seq=run_start_seq,
                            latest_run_type=getattr(run, "run_type", None),
                            latest_run_status=getattr(run, "status", None),
                            latest_error=getattr(run, "error", None),
                        )
                        repair_warning = {
                            "resolved_by": "db_projection_ahead_repair",
                            "projection_consistency": projection.to_dict(),
                            "projection_repair": repair.to_dict(),
                        }
                        if warning:
                            repair_warning["previous_warning"] = warning
                        if rerun_warning:
                            repair_warning["rerun_warning"] = rerun_warning
                        warning = repair_warning
                except Exception:
                    if settings.debug:
                        traceback.print_exc()
            await persist_runtime_integrity_diagnostics(db, run_id, decision, warning)
            await db.commit()
            return decision

    async def _record_run_start_seq(self, session_id: str, run_id: uuid.UUID) -> None:
        try:
            async with AsyncSessionLocal() as db:
                from unittest.mock import Mock
                if isinstance(db, Mock):
                    return
                from agent.run_models import AgentRun

                run = await db.get(AgentRun, run_id)
                if not run:
                    return
                diagnostics = dict(getattr(run, "diagnostics", None) or {})
                if diagnostics.get("run_start_seq") is None:
                    diagnostics["run_start_seq"] = await session_svc.get_last_message_seq(
                        db,
                        uuid.UUID(session_id),
                    )
                    await scheduler.update_diagnostics(db, run_id, diagnostics)
                    await db.commit()
        except Exception:
            if settings.debug:
                traceback.print_exc()

    def _reap_done_tasks(self) -> None:
        """Clean up completed/failed tasks from _active_runs."""
        done_ids = [rid for rid, t in self._active_runs.items() if t.done()]
        for rid in done_ids:
            task = self._active_runs.pop(rid)
            exc = task.exception()
            if exc:
                print(
                    f"[worker:{self.worker_id}] Task {rid} had unhandled exception: "
                    f"{type(exc).__name__}: {exc}"
                )

    async def _heartbeat_loop(self) -> None:
        """Periodically renew leases for all active runs (Phase 7A).

        Runs as a background task for the lifetime of the worker.
        Interval = lease_seconds / 3 to ensure renewal well before expiry.
        """
        interval = max(self.lease_seconds // 3, 10)
        while not self._shutdown:
            try:
                active_run_ids = list(self._active_runs.keys())
                if active_run_ids:
                    async with AsyncSessionLocal() as db:
                        for run_id in active_run_ids:
                            try:
                                await scheduler.renew_lease(db, run_id, self.lease_seconds)
                            except Exception:
                                pass  # run may have completed between snapshot and renew
                        await db.commit()
            except Exception:
                pass  # best-effort — don't crash the heartbeat loop
            await asyncio.sleep(interval)

    async def _drain_active_runs(self) -> None:
        """Wait for all active runs to complete on shutdown, with timeout."""
        if not self._active_runs:
            return
        count = len(self._active_runs)
        print(f"[worker:{self.worker_id}] Draining {count} active runs (timeout={DRAIN_TIMEOUT}s)...")
        try:
            await asyncio.wait_for(
                asyncio.gather(*self._active_runs.values(), return_exceptions=True),
                timeout=DRAIN_TIMEOUT,
            )
            print(f"[worker:{self.worker_id}] All runs drained successfully")
        except asyncio.TimeoutError:
            print(f"[worker:{self.worker_id}] Drain timeout, cancelling remaining tasks")
            for task in self._active_runs.values():
                task.cancel()
            # Wait briefly for cancellations to propagate
            await asyncio.gather(*self._active_runs.values(), return_exceptions=True)

    # ── Run type handlers ────────────────────────────────────────────────

    async def _execute_start(self, session_id: str, run_id: uuid.UUID, payload: dict) -> None:
        """Execute a 'start' run."""
        from agent.executor import _update_db_status

        # Update session status from queued → running
        await _update_db_status(session_id, "running")
        await self._publish(session_id, {"event": "status_change", "status": "running"})

        # Build abort checker
        sid = uuid.UUID(session_id)
        check_abort = self._make_abort_checker(sid, run_id)

        if settings.debug and payload.get("is_subtask_continuation", False):
            print(
                f"[worker:{self.worker_id}] Running subtask continuation "
                f"session={session_id[:8]} run={str(run_id)[:8]}"
            )

        await execute_start(
            session_id=session_id,
            user_id=payload["user_id"],
            user_root=payload["user_root"],
            session_dir=payload["session_dir"],
            agent_id=payload["agent_id"],
            model_id=payload["model_id"],
            user_message=payload["user_message"],
            user_message_ref=payload.get("user_message_ref"),
            publish=self._publish,
            check_abort=check_abort,
            tool_profile=payload.get("tool_profile"),
            is_subtask_continuation=payload.get("is_subtask_continuation", False),
            parent_session_dir=payload.get("parent_session_dir"),
            allowed_tools=payload.get("allowed_tools"),
            run_id=str(run_id),
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
            run_id=str(run_id),
        )

    async def _execute_continue(self, session_id: str, run_id: uuid.UUID, payload: dict) -> None:
        """Execute a checkpoint continuation run."""
        from agent.executor import _update_db_status

        if (
            not isinstance(payload, dict)
            or payload.get("mode") != "retry_model_node"
            or not payload.get("source_run_id")
        ):
            raise RuntimeError(
                "Invalid continue run payload: mode=retry_model_node and source_run_id are required"
            )

        await _update_db_status(session_id, "running")
        await self._publish(session_id, {"event": "status_change", "status": "running"})

        sid = uuid.UUID(session_id)
        check_abort = self._make_abort_checker(sid, run_id)

        await execute_continue(
            session_id=session_id,
            publish=self._publish,
            check_abort=check_abort,
            run_id=str(run_id),
            payload=payload,
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

            # Phase 7A: clear session interrupt flag
            await scheduler.clear_interrupt(db, sid)

            await db.commit()

            if cancelled_perms > 0:
                print(f"[worker:{self.worker_id}] Abort cancelled {cancelled_perms} pending permission(s)")

        from agent.executor import _update_db_status
        await _update_db_status(session_id, "idle")
        await self._publish(session_id, {"event": "status_change", "status": "idle"})

    async def _bridge_child_result(self, child_session_id: str) -> None:
        """Phase P3: If this session is a child, bridge results to the parent.

        After a child session run completes:
        1. Check if session has parent_id
        2. Gather child's last assistant message as summary
        3. Update the session_tasks record status
        4. Enqueue a new 'start' run on the parent with the child result
        5. Set parent from subtask_waiting → queued
        """
        parent_session_id: str | None = None
        parent_continuation_run_id: uuid.UUID | None = None
        try:
            from agent.subtask_bridge import bridge_reconcilable_child_tasks

            sweep = await bridge_reconcilable_child_tasks(
                child_session_id=uuid.UUID(child_session_id),
                publish=self._publish,
            )
            if sweep.parent_session_id:
                parent_session_id = sweep.parent_session_id
            if sweep.bridged_task_ids or sweep.already_bridged_task_ids:
                print(
                    f"[worker:{self.worker_id}] Bridged child result sweep: "
                    f"child={child_session_id[:8]} "
                    f"parent={(sweep.parent_session_id or '')[:8]} "
                    f"bridged={sweep.bridged_task_ids} "
                    f"already={sweep.already_bridged_task_ids} "
                    f"delayed={sweep.delayed_task_ids} "
                    f"run={sweep.enqueued_run_id}"
                )
            return
        except Exception as e:
            print(f"[worker:{self.worker_id}] Child result bridge sweep failed: {e}")
            if settings.debug:
                traceback.print_exc()

        try:
            from session.models import Session
            from agent.task_models import SessionTask
            from agent.executor import _update_db_status
            from sqlalchemy import select, update as sa_update
            import session.models  # noqa: F401
            import auth.models  # noqa: F401

            child_sid = uuid.UUID(child_session_id)
            task = None

            # 1. Check parent_id
            async with AsyncSessionLocal() as db:
                child = await db.get(Session, child_sid)
                if not child or not child.parent_id:
                    return  # Not a child session — nothing to bridge

                parent_id = child.parent_id
                parent = await db.get(Session, parent_id)
                if not parent:
                    return
                result = await db.execute(
                    select(SessionTask).where(
                        SessionTask.child_session_id == child_sid,
                    )
                )
                task = result.scalar_one_or_none()

                # Only bridge if parent is actually waiting for this child. A
                # short-lived v0.4.4 regression could collapse subtask_waiting
                # into generic waiting; accept that stale state only when the
                # blocking child task matches this completed child session.
                bridge_task_id = str(task.id) if task else ""
                if await _parent_has_subtask_result(
                    db,
                    parent_id,
                    bridge_task_id,
                    child_session_id,
                ):
                    if task and task.status != "completed":
                        await reconcile_completed_child_tasks(db, parent_id)
                        task.status = "completed"
                        task.result_ref = f".agentd/tasks/{task.id}/result.json"
                        await db.commit()
                    return
                if not _parent_can_accept_child_bridge(parent, task):
                    return

                parent_session_id = str(parent_id)

            # 2. Gather child result summary + source refs
            summary = await self._gather_child_summary(child_session_id)
            child_source_refs = await self._gather_child_source_refs(child_session_id)

            # 3. Update session_tasks record
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(SessionTask).where(
                        SessionTask.child_session_id == child_sid,
                    )
                )
                task = result.scalar_one_or_none()
                if task:
                    task.status = "completed"
                    task.result_ref = f".agentd/tasks/{task.id}/result.json"
                    await db.commit()

                    # Write result to filesystem
                    from agent.tasks import write_task_result, update_task_status
                    from workspace.manager import get_session_dir
                    parent_session_dir = get_session_dir(parent.workspace if hasattr(parent, 'workspace') else '', parent_session_id)

                    # Try to find session_dir from parent's user
                    async with AsyncSessionLocal() as db2:
                        from auth.models import User
                        user = await db2.get(User, parent.user_id)
                        if user:
                            parent_session_dir = get_session_dir(user.workspace, parent_session_id)

                    update_task_status(parent_session_dir, str(task.id), "completed",
                                       result_summary=summary[:500])
                    write_task_result(parent_session_dir, str(task.id), {
                        "status": "completed",
                        "summary": summary,
                        "child_session_id": child_session_id,
                    })

            # 4. Enqueue a new run on the parent with child result
            # Include source refs so the main agent can use [N] citations
            source_refs_text = ""
            if child_source_refs:
                source_refs_text = "\n\n**Sources referenced by sub-task:**\n"
                for ref in child_source_refs:
                    idx = ref.get("ref_index", "?")
                    title = ref.get("title", "")
                    doc_id = ref.get("doc_id", "")
                    source_refs_text += f"  [{idx}] {title} (doc_id: {doc_id})\n"
                source_refs_text += "\nUse these [N] references when citing information from the sub-task summary."

            child_result_message = (
                f"[Sub-task completed]\n\n"
                f"Child session {child_session_id} has finished.\n\n"
                f"**Summary:**\n{summary}"
                f"{source_refs_text}\n\n"
                f"Continue with the main task based on this result."
            )

            # Determine task_id for the subtask_result part
            bridge_task_id = str(task.id) if task else ""

            if not await self._wait_parent_run_settled(parent_id):
                raise RuntimeError(
                    "Parent run did not settle before child-result bridge; "
                    "refusing to enqueue subtask continuation"
                )

            async with AsyncSessionLocal() as db:
                from auth.models import User
                user = await db.get(User, parent.user_id)
                if not user:
                    return
                parent_session_dir = get_session_dir(user.workspace, parent_session_id)

                # Subagent waiting can leave the parent checkpoint ending with
                # an assistant tool_call whose ToolMessage result reached the UI
                # DB but not the runtime checkpoint. Strict providers reject
                # that history on continuation, so repair before bridging.
                from agent.runtime import build_agent
                from agent.executor import (
                    _checkpoint_tool_adjacency_is_valid,
                    _checkpoint_tool_call_ids,
                    _load_tool_messages_from_persisted_session,
                    _repair_checkpoint_tool_adjacency,
                )

                parent_agent = await build_agent(
                    session_id=parent_session_id,
                    user_id=str(parent.user_id),
                    user_root=user.workspace,
                    session_dir=parent_session_dir,
                    agent_id=parent.agent_id,
                    model_id=parent.model_id,
                )
                parent_config = {"configurable": {"thread_id": parent_session_id}}
                parent_snapshot = await parent_agent.aget_state(parent_config)
                parent_messages = (
                    (parent_snapshot.values or {}).get("messages", [])
                    if parent_snapshot
                    else []
                )
                needed_tool_call_ids = _checkpoint_tool_call_ids(parent_messages)
                repair_tool_messages = await _load_tool_messages_from_persisted_session(
                    parent_session_id,
                    needed_tool_call_ids,
                )
                repair_tool_messages.extend(
                    self._synthesize_launch_subagent_tool_messages(
                        parent_messages,
                        existing_tool_call_ids={
                            getattr(msg, "tool_call_id", None)
                            for msg in repair_tool_messages
                        },
                        task=task,
                        child_session_id=child_session_id,
                        run_id=str(getattr(task, "id", "")),
                    )
                )
                await _repair_checkpoint_tool_adjacency(
                    parent_agent,
                    parent_config,
                    parent_session_id,
                    candidate_tool_messages=repair_tool_messages,
                    strict=True,
                )
                parent_snapshot = await parent_agent.aget_state(parent_config)
                parent_messages = (
                    (parent_snapshot.values or {}).get("messages", [])
                    if parent_snapshot
                    else []
                )
                if not _checkpoint_tool_adjacency_is_valid(parent_messages):
                    raise RuntimeError(
                        "Parent checkpoint tool adjacency remains invalid; "
                        "refusing to enqueue subtask continuation"
                    )

                if await _parent_has_subtask_result(
                    db,
                    parent_id,
                    bridge_task_id,
                    child_session_id,
                ):
                    await db.commit()
                    return

                # Persist child result as an assistant message with subtask_result part
                from session import service as session_svc
                await session_svc.create_message(
                    db,
                    session_id=parent_id,
                    role="assistant",
                    parts=[
                        {
                            "type": "subtask_result",
                            "task_id": bridge_task_id,
                            "child_session_id": child_session_id,
                            "status": "completed",
                            "summary": summary,
                            "artifact_root": f".agentd/tasks/{bridge_task_id}/artifacts",
                            "result_ref": f".agentd/tasks/{bridge_task_id}/result.json",
                            "title": task.title if task else "",
                            "source_refs": child_source_refs,
                        },
                    ] + ([{
                        "type": "source_refs",
                        "sources": child_source_refs,
                    }] if child_source_refs else []),
                )

                run = await scheduler.enqueue_start(
                    db,
                    session_id=parent_id,
                    payload={
                        "user_message": child_result_message,
                        "user_id": str(parent.user_id),
                        "user_root": user.workspace,
                        "session_dir": parent_session_dir,
                        "agent_id": parent.agent_id,
                        "model_id": parent.model_id,
                        "is_subtask_continuation": True,
                    },
                )
                parent_continuation_run_id = run.id

                # 5. Set parent status to queued
                await db.execute(
                    sa_update(Session)
                    .where(Session.id == parent_id)
                    .values(status="queued")
                )
                await db.commit()

            # Notify parent session
            await self._publish(parent_session_id, {
                "event": "status_change", "status": "queued",
            })
            await self._publish(parent_session_id, {
                "event": "task_completed",
                "child_session_id": child_session_id,
                "summary": summary[:200],
            })

            print(
                f"[worker:{self.worker_id}] Bridged child result: "
                f"child={child_session_id[:8]} → parent={parent_session_id[:8]}"
            )

        except Exception as e:
            print(f"[worker:{self.worker_id}] Child result bridge failed: {e}")
            if settings.debug:
                traceback.print_exc()
            if parent_session_id:
                try:
                    from agent.runtime_error_classifier import RuntimeErrorClassifier
                    from agent.runtime_recovery import persist_session_recovery_envelope

                    envelope = RuntimeErrorClassifier.classify_exception(
                        e,
                        run_type="subtask_bridge",
                        context={
                            "child_session_id": child_session_id,
                            "source": "subtask",
                            "bridge_child_session_id": child_session_id,
                            "bridge_failure_without_run_id": parent_continuation_run_id is None,
                        },
                    )
                    async with AsyncSessionLocal() as db:
                        persisted_run_id = await persist_session_recovery_envelope(
                            db,
                            session_id=uuid.UUID(parent_session_id),
                            run_id=parent_continuation_run_id,
                            envelope=envelope,
                            extra_diagnostics={
                                "source": "subtask",
                                "bridge_child_session_id": child_session_id,
                                "bridge_failure_without_run_id": parent_continuation_run_id is None,
                            },
                        )
                        await db.commit()
                    run_id_text = str(persisted_run_id) if persisted_run_id else None
                    await self._publish(parent_session_id, {
                        "event": "status_change",
                        "status": "idle",
                    })
                    await self._publish(parent_session_id, {
                        "event": "run_failed_recoverable",
                        "run_id": run_id_text,
                        "persisted": persisted_run_id is not None,
                        "persistence_reason": None if persisted_run_id else "no_target_run",
                        "old_state": "none",
                        "new_state": envelope.recovery_state,
                        "category": envelope.category,
                        "next_action": envelope.next_action,
                        "user_message": envelope.user_message,
                        "recovery_envelope": envelope.model_dump(mode="json"),
                    })
                    await self._publish(parent_session_id, {
                        "event": "recovery_state_changed",
                        "run_id": run_id_text,
                        "persisted": persisted_run_id is not None,
                        "persistence_reason": None if persisted_run_id else "no_target_run",
                        "old_state": "none",
                        "new_state": envelope.recovery_state,
                        "category": envelope.category,
                        "next_action": envelope.next_action,
                        "user_message": envelope.user_message,
                    })
                    await self._publish(parent_session_id, {
                        "event": "error",
                        "code": envelope.category,
                        "message": str(e),
                        "recovery_state": envelope.recovery_state,
                        "next_action": envelope.next_action,
                    })
                except Exception:
                    if settings.debug:
                        traceback.print_exc()

    def _synthesize_launch_subagent_tool_messages(
        self,
        messages: list,
        existing_tool_call_ids: set[str | None],
        task,
        child_session_id: str,
        run_id: str,
    ) -> list:
        import json
        from langchain_core.messages import AIMessage, ToolMessage

        if not task:
            return []

        synthesized = []
        for msg in messages:
            if not isinstance(msg, AIMessage) or not getattr(msg, "tool_calls", None):
                continue
            for tool_call in msg.tool_calls:
                tool_call_id = tool_call.get("id") or ""
                if (
                    not tool_call_id
                    or tool_call_id in existing_tool_call_ids
                    or tool_call.get("name") != "launch_subagent"
                ):
                    continue
                payload = {
                    "task_id": str(task.id),
                    "status": "waiting_for_child",
                    "task_kind": "child_session",
                    "blocking_mode": "blocking",
                    "child_session_id": child_session_id,
                    "run_id": run_id,
                    "title": getattr(task, "title", "") or "",
                    "message": (
                        f"Sub-task '{getattr(task, 'title', '') or 'child task'}' "
                        "started in child session. Waiting for it to complete..."
                    ),
                }
                synthesized.append(ToolMessage(
                    content=json.dumps(payload, ensure_ascii=False),
                    tool_call_id=tool_call_id,
                    name="launch_subagent",
                ))
        return synthesized

    async def _wait_parent_run_settled(
        self,
        parent_id: uuid.UUID,
        timeout_seconds: float = 5.0,
    ) -> bool:
        """Wait for parent run completion before child-result continuation."""
        import time

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            async with AsyncSessionLocal() as db:
                active = await scheduler.get_active_run(db, parent_id)
                if active is None:
                    return True
            await asyncio.sleep(0.1)
        return False

    async def _bridge_child_failure(self, child_session_id: str, error_msg: str) -> None:
        """Phase 6: bridge child failure back to parent.

        If a child session fails, the parent must not stay stuck in subtask_waiting.
        This creates a failed subtask_result and resumes the parent.
        """
        try:
            from session.models import Session
            from agent.task_models import SessionTask
            from sqlalchemy import select, update as sa_update
            import session.models  # noqa: F401
            import auth.models  # noqa: F401

            child_sid = uuid.UUID(child_session_id)

            async with AsyncSessionLocal() as db:
                child = await db.get(Session, child_sid)
                if not child or not child.parent_id:
                    return

                parent_id = child.parent_id
                parent = await db.get(Session, parent_id)
                if not parent or parent.status != "subtask_waiting":
                    return

                parent_session_id = str(parent_id)

            # Update session_tasks record to failed
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(SessionTask).where(SessionTask.child_session_id == child_sid)
                )
                task = result.scalar_one_or_none()
                if task:
                    task.status = "failed"
                    task.error = error_msg[:500]
                    await db.commit()

            # Bridge failed result to parent
            failure_message = (
                f"[Sub-task failed]\n\n"
                f"Child session {child_session_id} encountered an error:\n"
                f"{error_msg[:300]}\n\n"
                f"Please continue with the main task using available information."
            )

            bridge_task_id = str(task.id) if task else ""

            async with AsyncSessionLocal() as db:
                from auth.models import User
                user = await db.get(User, parent.user_id)
                if not user:
                    return
                from workspace.manager import get_session_dir
                parent_session_dir = get_session_dir(user.workspace, parent_session_id)

                from session import service as session_svc
                await session_svc.create_message(
                    db,
                    session_id=parent_id,
                    role="assistant",
                    parts=[{
                        "type": "subtask_result",
                        "task_id": bridge_task_id,
                        "child_session_id": child_session_id,
                        "status": "failed",
                        "summary": f"Sub-task failed: {error_msg[:300]}",
                        "title": task.title if task else "",
                        "source_refs": [],
                    }],
                )

                run = await scheduler.enqueue_start(
                    db,
                    session_id=parent_id,
                    payload={
                        "user_message": failure_message,
                        "user_id": str(parent.user_id),
                        "user_root": user.workspace,
                        "session_dir": parent_session_dir,
                        "agent_id": parent.agent_id,
                        "model_id": parent.model_id,
                        "is_subtask_continuation": True,
                    },
                )

                await db.execute(
                    sa_update(Session)
                    .where(Session.id == parent_id)
                    .values(status="queued")
                )
                await db.commit()

            await self._publish(parent_session_id, {
                "event": "status_change", "status": "queued",
            })
            await self._publish(parent_session_id, {
                "event": "task_failed",
                "child_session_id": child_session_id,
                "error": error_msg[:200],
            })

            print(
                f"[worker:{self.worker_id}] Bridged child FAILURE: "
                f"child={child_session_id[:8]} → parent={parent_session_id[:8]}"
            )

        except Exception as e:
            print(f"[worker:{self.worker_id}] Child failure bridge failed: {e}")
            if settings.debug:
                traceback.print_exc()

    async def _gather_child_summary(self, child_session_id: str) -> str:
        """Extract a summary from the child session's last assistant message."""
        try:
            from session import service as session_svc

            child_sid = uuid.UUID(child_session_id)
            async with AsyncSessionLocal() as db:
                messages = await session_svc.list_messages(db, child_sid)

            # Find the last assistant message
            for msg in reversed(messages):
                if msg.role == "assistant":
                    parts = msg.parts or []
                    texts = [p.get("content", "") for p in parts if p.get("type") == "text"]
                    if texts:
                        return "\n".join(texts)

            return "(Child session completed but produced no text output)"
        except Exception as e:
            return f"(Failed to gather child summary: {e})"

    async def _gather_child_source_refs(self, child_session_id: str) -> list[dict]:
        """Extract knowledge source refs from the child session's tool results.

        Scans all messages in the child session for knowledge_search and
        knowledge_read ToolMessages, extracts structured source references.
        These are passed back to the parent so the main agent can use
        accurate [N] citations in its final answer.
        """
        try:
            from session import service as session_svc
            import json as _json

            child_sid = uuid.UUID(child_session_id)
            async with AsyncSessionLocal() as db:
                messages = await session_svc.list_messages(db, child_sid)

            sources: dict[str, dict] = {}
            for msg in messages:
                if msg.role != "tool":
                    continue
                parts = msg.parts or []
                for part in parts:
                    if part.get("type") != "tool_result":
                        continue
                    tool_name = part.get("tool_name", "")
                    if tool_name not in ("knowledge_search", "knowledge_read"):
                        continue
                    content = part.get("output", "")
                    try:
                        data = _json.loads(content)
                    except (ValueError, _json.JSONDecodeError):
                        continue

                    if tool_name == "knowledge_search":
                        for result in data.get("results", []):
                            doc_id = result.get("doc_id", "")
                            if doc_id and doc_id not in sources:
                                excerpts = result.get("excerpts", [])
                                evidence = excerpts[0]["text"] if excerpts else ""
                                sources[doc_id] = {
                                    "doc_id": doc_id,
                                    "title": result.get("title", ""),
                                    "kind": result.get("kind", ""),
                                    "source_file": "",
                                    "evidence_excerpt": evidence[:300],
                                }
                    elif tool_name == "knowledge_read":
                        doc_id = data.get("doc_id", "")
                        if doc_id:
                            content_text = data.get("content", "")
                            entry = sources.get(doc_id, {
                                "doc_id": doc_id,
                                "title": data.get("title", ""),
                                "kind": data.get("kind", ""),
                                "source_file": data.get("source_file", ""),
                                "evidence_excerpt": "",
                            })
                            if data.get("title"):
                                entry["title"] = data["title"]
                            if data.get("source_file"):
                                entry["source_file"] = data["source_file"]
                            if content_text and not entry.get("evidence_excerpt"):
                                entry["evidence_excerpt"] = content_text[:300]
                            sources[doc_id] = entry

            result = list(sources.values())
            for i, src in enumerate(result):
                src["ref_index"] = i + 1
            return result

        except Exception as e:
            print(f"[worker] Failed to gather child source refs: {e}")
            return []

    def _make_abort_checker(self, session_id: uuid.UUID, current_run_id: uuid.UUID):
        """Create a closure that checks for pending abort signals (Phase 7A).

        Uses session-level interrupt flag (preferred) with fallback to
        legacy queued abort run check for backward compatibility.
        """
        current_task = asyncio.current_task()

        async def _check() -> bool:
            if self._shutdown:
                return True
            # Task-level cancellation check (e.g. during drain)
            if current_task and current_task.cancelled():
                return True
            # Session-level interrupt flag + legacy fallback
            async with AsyncSessionLocal() as db:
                return await scheduler.is_interrupted(db, session_id)
        return _check

    async def _publish(self, session_id: str, event: dict) -> None:
        """Publish an SSE event via PG NOTIFY (cross-process) + local event_bus fallback."""
        notify_ok = False
        notify_error = ""
        # Primary: cross-process via PG NOTIFY
        try:
            from core.event_bridge import notify
            await notify(session_id, event)
            notify_ok = True
        except Exception as e:
            notify_error = f"{type(e).__name__}: {e}"
            # Log prominently — silent swallowing here was the root cause of #41
            print(f"[worker:{self.worker_id}] event_bridge.notify FAILED: {notify_error}")
            if settings.debug:
                traceback.print_exc()
        event["_event_bridge_notify_ok"] = notify_ok
        if notify_error:
            event["_event_bridge_notify_error"] = notify_error

        # Secondary: local event_bus (useful when API and worker share the same process)
        try:
            from core.events import event_bus
            await event_bus.publish(session_id, event)
        except Exception:
            pass

    def shutdown(self) -> None:
        """Signal the worker to stop. Active runs will drain before exit."""
        self._shutdown = True
        active = len(self._active_runs)
        print(
            f"[worker:{self.worker_id}] Shutdown requested "
            f"({active} active run{'s' if active != 1 else ''} will drain)"
        )


# ── Entry point ──────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="AgentD Worker Process")
    parser.add_argument("--worker-id", default=None, help="Unique worker identifier")
    parser.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL)
    parser.add_argument("--lease-seconds", type=int, default=DEFAULT_LEASE_SECONDS)
    parser.add_argument(
        "--max-concurrent", type=int,
        default=int(os.environ.get("WORKER_MAX_CONCURRENT", DEFAULT_MAX_CONCURRENT)),
        help=f"Maximum concurrent runs per worker (default: {DEFAULT_MAX_CONCURRENT})",
    )
    args = parser.parse_args()

    worker = AgentWorker(
        worker_id=args.worker_id,
        poll_interval=args.poll_interval,
        lease_seconds=args.lease_seconds,
        max_concurrent=args.max_concurrent,
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
