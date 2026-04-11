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
from agent.executor import execute_resume, execute_start
from core.config import settings
from core.database import AsyncSessionLocal


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

            elapsed = time.monotonic() - t0
            active_count = len(self._active_runs) - 1  # this one is finishing
            print(
                f"[worker:{self.worker_id}] [{active_count}/{self.max_concurrent}] "
                f"Completed run {run_id} in {elapsed:.1f}s"
            )

            # Child result bridge
            await self._bridge_child_result(session_id)

        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            elapsed = time.monotonic() - t0
            print(f"[worker:{self.worker_id}] Failed run {run_id} after {elapsed:.1f}s: {error_msg}")
            if settings.debug:
                traceback.print_exc()

            async with AsyncSessionLocal() as db:
                await scheduler.mark_failed(db, run_id, error_msg)
                await db.commit()

            from agent.executor import _update_db_status
            is_subtask_continuation = (
                run_type == "start" and payload.get("is_subtask_continuation", False)
            )
            next_status = "idle" if is_subtask_continuation else "error"
            await _update_db_status(session_id, next_status)
            await self._publish(session_id, {"event": "status_change", "status": next_status})
            await self._publish(session_id, {
                "event": "error",
                "code": (
                    "subtask_continuation_error"
                    if is_subtask_continuation
                    else "worker_error"
                ),
                "message": error_msg,
            })
            if is_subtask_continuation:
                print(
                    f"[worker:{self.worker_id}] Subtask continuation failed; "
                    f"restoring session {session_id[:8]} to idle so the bridged result remains usable"
                )

            # Child failure bridge
            await self._bridge_child_failure(session_id, error_msg)

        finally:
            # Release session slot
            sid = uuid.UUID(session_id)
            self._active_sessions.discard(sid)

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
        try:
            from session.models import Session
            from agent.task_models import SessionTask
            from agent.executor import _update_db_status
            from sqlalchemy import select, update as sa_update
            import session.models  # noqa: F401
            import auth.models  # noqa: F401

            child_sid = uuid.UUID(child_session_id)

            # 1. Check parent_id
            async with AsyncSessionLocal() as db:
                child = await db.get(Session, child_sid)
                if not child or not child.parent_id:
                    return  # Not a child session — nothing to bridge

                parent_id = child.parent_id
                parent = await db.get(Session, parent_id)
                if not parent:
                    return

                # Only bridge if parent is actually waiting
                if parent.status != "subtask_waiting":
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

            async with AsyncSessionLocal() as db:
                from auth.models import User
                user = await db.get(User, parent.user_id)
                if not user:
                    return
                parent_session_dir = get_session_dir(user.workspace, parent_session_id)

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
