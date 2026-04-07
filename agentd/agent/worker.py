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

            # Phase P3: child result bridge — if this session is a child,
            # push results back to the parent session
            await self._bridge_child_result(session_id)

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

            # Phase 6: child failure bridge — if this failed session is a child,
            # bridge the failure back to parent so it doesn't stay stuck in subtask_waiting
            await self._bridge_child_failure(session_id, error_msg)

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
            tool_profile=payload.get("tool_profile"),
            is_subtask_continuation=payload.get("is_subtask_continuation", False),
            parent_session_dir=payload.get("parent_session_dir"),
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
