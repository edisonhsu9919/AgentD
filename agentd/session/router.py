import asyncio
import json
import os
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from api.deps import get_current_user, require_admin
from auth.models import User
from core.database import get_db
from core.response import ok, ok_list
from session import service as session_svc
from session.schemas import (
    MessageResponse,
    PromptRequest,
    RuntimeResponse,
    SessionCreate,
    SessionResponse,
)

router = APIRouter()


# ── Session endpoints ────────────────────────────────────────────────────────


@router.post("")
async def create_session(
    body: SessionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from workspace.manager import get_session_dir

    # Resolve model_id: explicit > DB default > env fallback
    model_id = body.model_id
    if not model_id:
        from model_config.service import resolve_active_model_config
        resolved = await resolve_active_model_config(db)
        model_id = resolved.model_id

    session = await session_svc.create_session(
        db,
        user_id=current_user.id,
        model_id=model_id,
        title=body.title,
        agent_id=body.agent_id,
    )
    await db.commit()

    # Create session working directory immediately (§7.2, Phase 6.7)
    get_session_dir(current_user.workspace, str(session.id))

    return ok(SessionResponse.model_validate(session).model_dump(mode="json"))


@router.get("")
async def list_sessions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sessions, total = await session_svc.list_sessions(
        db, current_user.id, page=page, page_size=page_size
    )
    data = [SessionResponse.model_validate(s).model_dump(mode="json") for s in sessions]
    return ok_list(data, total=total, page=page, page_size=page_size)


@router.get("/{session_id}")
async def get_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = await session_svc.get_session(db, session_id)
    if not session or session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Session not found"},
        )
    return ok(SessionResponse.model_validate(session).model_dump(mode="json"))


@router.delete("/{session_id}")
async def delete_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Verify ownership first
    session = await session_svc.get_session(db, session_id)
    if not session or session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Session not found"},
        )
    await session_svc.delete_session(db, session_id)
    await db.commit()
    return ok({"deleted": True})


# ── Message endpoints ────────────────────────────────────────────────────────


@router.get("/{session_id}/messages")
async def list_messages(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Verify ownership
    session = await session_svc.get_session(db, session_id)
    if not session or session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Session not found"},
        )
    messages = await session_svc.list_messages(db, session_id)
    data = [MessageResponse.model_validate(m).model_dump(mode="json") for m in messages]
    return ok_list(data, total=len(data))


# ── Recovery endpoints (Phase A — state recovery) ───────────────────────────


@router.get("/{session_id}/runtime")
async def get_runtime(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return session runtime snapshot for frontend state recovery.

    Derives runtime state from existing tables — no new DB table needed.
    This is the primary recovery endpoint: frontend calls this after
    page refresh or SSE reconnect to restore the correct UI (Phase A §5.1).
    """
    from permission import service as perm_svc

    session = await session_svc.get_session(db, session_id)
    if not session or session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Session not found"},
        )

    last_seq = await session_svc.get_last_message_seq(db, session_id)
    pending_count = await perm_svc.count_pending_by_session(db, session_id)

    # Derive phase from status
    phase_map = {
        "idle": None,
        "queued": "queued",
        "running": "running",
        "waiting": "permission_waiting",
        "error": "error",
    }
    phase = phase_map.get(session.status)

    # Resumable: currently only when waiting for permission
    resumable = session.status == "waiting" and pending_count > 0

    # Phase L: fetch latest run diagnostics for context occupancy
    ctx_prompt = 0
    ctx_completion = 0
    ctx_window = None
    ctx_ratio = None
    try:
        from agent.run_models import AgentRun
        from sqlalchemy import select as sa_select

        stmt = (
            sa_select(AgentRun)
            .where(AgentRun.session_id == session_id)
            .where(AgentRun.diagnostics.isnot(None))
            .order_by(AgentRun.updated_at.desc())
            .limit(1)
        )
        last_run = (await db.execute(stmt)).scalar_one_or_none()
        if last_run and last_run.diagnostics:
            diag = last_run.diagnostics
            ctx_prompt = diag.get("last_call_prompt_tokens", 0)
            ctx_completion = diag.get("last_call_completion_tokens", 0)
            ctx_window = diag.get("context_window_limit")
            ctx_ratio = diag.get("context_usage_ratio")
    except Exception:
        pass  # Graceful fallback — no diagnostics available yet

    # Phase N1: read compaction state from context_summary.json
    last_compaction_at = None
    compaction_count = 0
    try:
        from datetime import datetime as _dt
        from workspace.manager import get_session_dir

        session_dir = get_session_dir(current_user.workspace, str(session_id))
        summary_path = os.path.join(session_dir, ".agentd", "context_summary.json")
        if os.path.isfile(summary_path):
            with open(summary_path, "r", encoding="utf-8") as f:
                cs = json.load(f)
            ts = cs.get("compacted_at")
            if ts:
                last_compaction_at = _dt.fromisoformat(ts)
            compaction_count = cs.get("compaction_count", 1)
    except Exception:
        pass

    runtime = RuntimeResponse(
        session_id=session.id,
        status=session.status,
        phase=phase,
        last_message_seq=last_seq,
        pending_permissions_count=pending_count,
        resumable=resumable,
        last_error=None,  # Phase A: to be enhanced later
        updated_at=session.updated_at,
        last_call_prompt_tokens=ctx_prompt,
        last_call_completion_tokens=ctx_completion,
        context_window_limit=ctx_window,
        context_usage_ratio=ctx_ratio,
        last_compaction_at=last_compaction_at,
        compaction_count=compaction_count,
    )
    return ok(runtime.model_dump(mode="json"))


@router.get("/{session_id}/permissions/pending")
async def list_pending_permissions(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return all pending permission requests for a session.

    This endpoint enables frontend recovery of the waiting/approval UI
    after page refresh or SSE disconnect (Phase A §6.1).
    """
    from permission import service as perm_svc
    from permission.schemas import PendingPermissionResponse

    session = await session_svc.get_session(db, session_id)
    if not session or session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Session not found"},
        )

    pending = await perm_svc.get_pending_by_session(db, session_id)
    data = [
        PendingPermissionResponse.model_validate(p).model_dump(mode="json")
        for p in pending
    ]
    return ok_list(data, total=len(data))


# ── Policy endpoints (Phase B — permission modes) ───────────────────────────


class PolicyPatchRequest(BaseModel):
    """Request body for PATCH /api/sessions/{id}/policy."""
    mode: Optional[str] = None  # "manual" | "autopilot" | "fsd"
    reset_rules: bool = False


@router.get("/{session_id}/policy")
async def get_policy(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the session permission policy (mode + rules).

    Frontend uses this to display autopilot/fsd status and rule list (Phase B).
    """
    from permission.policy import load_policy
    from workspace.manager import get_session_dir

    session = await session_svc.get_session(db, session_id)
    if not session or session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Session not found"},
        )

    session_dir = get_session_dir(current_user.workspace, str(session_id))
    policy = load_policy(session_dir)
    return ok(policy.model_dump())


@router.patch("/{session_id}/policy")
async def patch_policy(
    session_id: uuid.UUID,
    body: PolicyPatchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Modify the session permission policy.

    Supports:
    - Switching mode: manual / autopilot / fsd
    - Resetting all rules (reset_rules=true)
    - Combining both: e.g. switch to manual + clear rules
    """
    from permission.policy import load_policy, save_policy

    session = await session_svc.get_session(db, session_id)
    if not session or session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Session not found"},
        )

    from workspace.manager import get_session_dir
    session_dir = get_session_dir(current_user.workspace, str(session_id))
    policy = load_policy(session_dir)

    if body.mode and body.mode in ("manual", "autopilot", "fsd"):
        policy.mode = body.mode

    if body.reset_rules:
        policy.rules = []
        # If resetting rules and not explicitly setting mode, revert to manual
        if not body.mode:
            policy.mode = "manual"

    save_policy(session_dir, policy)
    return ok(policy.model_dump())


# ── Task plan endpoints (Phase E) ──────────────────────────────────────────


@router.get("/{session_id}/task-plan")
async def get_task_plan(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the session task plan (planning/todo subsystem).

    Returns the task_plan.json content if it exists, or a default empty plan.
    """
    import json

    from workspace.manager import get_session_dir

    session = await session_svc.get_session(db, session_id)
    if not session or session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Session not found"},
        )

    session_dir = get_session_dir(current_user.workspace, str(session_id))
    plan_path = os.path.join(session_dir, ".agentd", "task_plan.json")

    if not os.path.isfile(plan_path):
        return ok({"active": False, "task": {"title": "", "summary": ""}, "steps": []})

    try:
        with open(plan_path, "r", encoding="utf-8") as f:
            plan = json.load(f)
        return ok(plan)
    except (json.JSONDecodeError, OSError):
        return ok({"active": False, "task": {"title": "", "summary": ""}, "steps": []})


@router.delete("/{session_id}/task-plan")
async def delete_task_plan(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Clear the session task plan."""
    from workspace.manager import get_session_dir

    session = await session_svc.get_session(db, session_id)
    if not session or session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Session not found"},
        )

    session_dir = get_session_dir(current_user.workspace, str(session_id))
    plan_path = os.path.join(session_dir, ".agentd", "task_plan.json")

    if os.path.isfile(plan_path):
        os.remove(plan_path)

    return ok({"deleted": True})


# ── Agent loop endpoints (Phase 4) ──────────────────────────────────────────


@router.post("/{session_id}/prompt")
async def send_prompt(
    session_id: uuid.UUID,
    body: PromptRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Send a user message and enqueue a 'start' run for the worker.

    Phase C: API no longer executes the agent loop directly.
    It persists the user message and enqueues an agent_run(run_type=start).
    """
    from agent.scheduler import enqueue_start
    from workspace.manager import get_session_dir

    session = await session_svc.get_session(db, session_id)
    if not session or session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Session not found"},
        )

    if session.status in ("running", "waiting", "queued"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CONFLICT",
                "message": "Session is already running"
                if session.status == "running"
                else "Session is waiting for permission approval"
                if session.status == "waiting"
                else "Session has a queued run",
            },
        )

    # Persist the user message
    msg = await session_svc.create_message(
        db,
        session_id=session_id,
        role="user",
        parts=[{"type": "text", "content": body.content}],
    )

    # Enqueue start run — worker will claim and execute
    session_dir = get_session_dir(current_user.workspace, str(session_id))
    run = await enqueue_start(db, session_id, payload={
        "user_message": body.content,
        "user_id": str(current_user.id),
        "user_root": current_user.workspace,
        "session_dir": session_dir,
        "agent_id": session.agent_id,
        "model_id": session.model_id,
    })

    await session_svc.update_session_status(db, session_id, "queued")
    await db.commit()

    return ok({"message_id": str(msg.id), "run_id": str(run.id), "status": "queued"})


@router.get("/{session_id}/events")
async def sse_events(
    session_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """SSE endpoint — streams real-time events for a session (§6).

    NOTE: We do NOT use Depends(get_db) here. The SSE generator runs
    indefinitely, which would hold a DB session open for the entire
    connection lifetime, exhausting the connection pool. Instead, we
    open a short-lived session just for the ownership check.
    """
    from core.database import AsyncSessionLocal
    from core.events import event_bus

    # Short-lived DB session for ownership check only
    async with AsyncSessionLocal() as db:
        session = await session_svc.get_session(db, session_id)
        if not session or session.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "NOT_FOUND", "message": "Session not found"},
            )

    queue = await event_bus.subscribe(str(session_id))

    async def _event_generator():
        try:
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield {
                        "event": event.get("event", "message"),
                        "data": json.dumps(event, default=str),
                    }
                    # Stop streaming after "done" or terminal "error"
                    if event.get("event") in ("done",):
                        break
                except asyncio.TimeoutError:
                    # Send keepalive comment
                    yield {"comment": "keepalive"}
        finally:
            event_bus.remove(str(session_id))

    return EventSourceResponse(_event_generator())


@router.delete("/{session_id}/abort")
async def abort_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Abort a running agent loop.

    Phase C: Enqueues an abort run + cancels any queued (unclaimed) runs.
    The owning worker detects the abort at its next boundary check.
    """
    from agent.scheduler import cancel_queued_runs, enqueue_abort

    session = await session_svc.get_session(db, session_id)
    if not session or session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Session not found"},
        )

    if session.status not in ("running", "waiting", "queued"):
        return ok({"aborted": False, "reason": "Session is not active"})

    # Cancel any queued runs that haven't been claimed yet
    cancelled_count = await cancel_queued_runs(db, session_id)

    # If session was merely queued (no worker claimed yet), just reset to idle
    if session.status == "queued" and cancelled_count > 0:
        # Also cancel any pending permissions (#42)
        from permission import service as perm_svc
        await perm_svc.cancel_pending_by_session(db, session_id)
        await session_svc.update_session_status(db, session_id, "idle")
        await db.commit()
        return ok({"aborted": True})

    # For running/waiting sessions, enqueue an abort signal for the worker
    await enqueue_abort(db, session_id)
    await db.commit()

    return ok({"aborted": True})


# ── Unified task cancellation (Phase L) ──────────────────────────────────────


@router.delete("/{session_id}/cancel-task")
async def cancel_task(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Unified task cancellation: stop current run + clear plan + reset state.

    Phase L: Combines abort + plan reset into a single atomic operation,
    ensuring the session returns to a clean state without residual plan context.

    Handles all session states:
    - queued: cancel queued runs, reset to idle
    - waiting: cancel pending permissions, reset to idle
    - running: enqueue abort for the worker
    - idle/error: no-op for abort, still clears plan
    """
    from agent.scheduler import cancel_queued_runs, enqueue_abort
    from core.events import event_bus
    from workspace.manager import get_session_dir

    session = await session_svc.get_session(db, session_id)
    if not session or session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Session not found"},
        )

    result = {"aborted": False, "plan_cleared": False, "status": session.status}

    # 1. Stop current run (if active)
    if session.status in ("running", "waiting", "queued"):
        await cancel_queued_runs(db, session_id)

        if session.status in ("queued", "waiting"):
            # No active worker — cancel permissions + reset directly
            from permission import service as perm_svc
            await perm_svc.cancel_pending_by_session(db, session_id)
            await session_svc.update_session_status(db, session_id, "idle")
            result["status"] = "idle"
            # Notify any connected SSE listeners
            await event_bus.publish(str(session_id), {
                "event": "status_change", "status": "idle",
            })
        else:
            # Running: enqueue abort for the worker to pick up
            await enqueue_abort(db, session_id)

        result["aborted"] = True

    # 2. Clear task plan
    session_dir = get_session_dir(current_user.workspace, str(session_id))
    plan_path = os.path.join(session_dir, ".agentd", "task_plan.json")
    if os.path.isfile(plan_path):
        os.remove(plan_path)
        result["plan_cleared"] = True

    await db.commit()
    return ok(result)


# ── Context compaction (Phase N1) ─────────────────────────────────────────────


@router.post("/{session_id}/compact")
async def compact_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually trigger context compaction for a session.

    Phase N1: Only allowed when session is idle — prevents conflicts with
    an active agent run that may be reading/writing checkpoint state.
    """
    from agent.compaction import compact_session as do_compact
    from agent.runtime import build_agent
    from core.events import event_bus
    from workspace.manager import get_session_dir

    session = await session_svc.get_session(db, session_id)
    if not session or session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Session not found"},
        )

    if session.status != "idle":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CONFLICT",
                "message": f"Cannot compact while session is {session.status}",
            },
        )

    session_dir = get_session_dir(current_user.workspace, str(session_id))

    # Build agent to access checkpoint state
    agent = await build_agent(
        session_id=str(session_id),
        user_id=str(current_user.id),
        user_root=current_user.workspace,
        session_dir=session_dir,
        agent_id=session.agent_id,
        model_id=session.model_id,
    )
    config = {"configurable": {"thread_id": str(session_id)}}

    result = await do_compact(
        agent=agent,
        config=config,
        session_id=str(session_id),
        session_dir=session_dir,
        model_id=session.model_id,
        publish=event_bus.publish,
    )

    return ok(result)


# ── Run history (Phase L — admin diagnostics) ──────────────────────────────


@router.get("/{session_id}/runs")
async def list_session_runs(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """List all agent runs for a session with payload, diagnostics, and status.

    Phase L: Admin-only endpoint for prompt continuity analysis and debugging.
    Returns runs ordered by created_at ascending (chronological).
    """
    from agent.run_models import AgentRun
    from sqlalchemy import select

    session = await session_svc.get_session(db, session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Session not found"},
        )

    stmt = (
        select(AgentRun)
        .where(AgentRun.session_id == session_id)
        .order_by(AgentRun.created_at.asc())
    )
    rows = (await db.execute(stmt)).scalars().all()

    return ok([
        {
            "id": str(r.id),
            "run_type": r.run_type,
            "status": r.status,
            "worker_id": r.worker_id,
            "payload": r.payload,
            "diagnostics": r.diagnostics,
            "error": r.error,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in rows
    ])
