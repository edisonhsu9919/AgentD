import asyncio
import json
import logging
import os
import shutil
import uuid
from dataclasses import dataclass
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
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
logger = logging.getLogger(__name__)


@dataclass
class _OpenHitlRecovery:
    action: str
    decisions: list[dict] | None = None
    reason: str = ""


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
    try:
        result = await session_svc.delete_session_tree(
            db,
            session_id,
            current_user.id,
        )
    except session_svc.SessionTreeBusyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "SESSION_BUSY",
                "message": (
                    "Session or child sessions are still running. "
                    "Cancel or wait before deleting."
                ),
                "blocking_session_ids": [
                    str(blocking_id) for blocking_id in exc.blocking_session_ids
                ],
            },
        ) from exc
    except session_svc.SessionTreeOwnershipError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Session not found"},
        ) from exc

    await db.commit()

    deleted_session_ids = [str(deleted_id) for deleted_id in result.deleted_session_ids]
    _cleanup_deleted_session_dirs(current_user.workspace, deleted_session_ids)
    return ok({
        "deleted": result.deleted_count > 0,
        "deleted_session_ids": deleted_session_ids,
        "deleted_count": result.deleted_count,
    })


def _cleanup_deleted_session_dirs(user_root: str, session_ids: list[str]) -> None:
    sessions_root = os.path.realpath(os.path.join(user_root, "sessions"))
    for session_id in session_ids:
        session_dir = os.path.realpath(os.path.join(sessions_root, session_id))
        try:
            if os.path.commonpath([sessions_root, session_dir]) != sessions_root:
                logger.warning(
                    "Refusing to cleanup session dir outside user root: %s",
                    session_dir,
                )
                continue
            shutil.rmtree(session_dir, ignore_errors=False)
        except FileNotFoundError:
            continue
        except Exception as exc:
            logger.warning(
                "Failed to cleanup deleted session dir %s: %s",
                session_dir,
                exc,
            )


def _diagnostics_allow_model_retry(diagnostics: Optional[dict]) -> bool:
    if not isinstance(diagnostics, dict):
        return False
    if not diagnostics.get("recoverable_model_continuation"):
        return False
    if diagnostics.get("checkpoint_valid") is False:
        return False
    next_nodes = diagnostics.get("checkpoint_next") or []
    return any(
        str(node) == "model"
        or str(node).endswith(".model")
        or str(node).endswith(":model")
        for node in next_nodes
    )


def _error_looks_like_provider_timeout(error: Optional[str]) -> bool:
    return bool(error and "timeout" in error.lower())


async def _checkpoint_allows_model_retry(session, current_user: User) -> bool:
    try:
        from agent.executor import (
            _checkpoint_tool_adjacency_is_valid,
            _snapshot_next_contains_model,
        )
        from agent.runtime import build_agent
        from langchain_core.messages import ToolMessage
        from workspace.manager import get_session_dir

        session_id = str(session.id)
        session_dir = get_session_dir(current_user.workspace, session_id)
        agent = await build_agent(
            session_id=session_id,
            user_id=str(current_user.id),
            user_root=current_user.workspace,
            session_dir=session_dir,
            agent_id=session.agent_id,
            model_id=session.model_id,
        )
        snapshot = await agent.aget_state({"configurable": {"thread_id": session_id}})
        messages = (snapshot.values or {}).get("messages", []) if snapshot else []
        if not snapshot or not _snapshot_next_contains_model(snapshot):
            return False
        if getattr(snapshot, "interrupts", None):
            return False
        if not messages or not isinstance(messages[-1], ToolMessage):
            return False
        return _checkpoint_tool_adjacency_is_valid(messages)
    except Exception:
        return False


async def _inspect_open_hitl_recovery(
    db: AsyncSession,
    session,
    current_user: User,
) -> _OpenHitlRecovery:
    """Detect an error-state session that still needs checkpoint resume."""
    try:
        from agent.executor import _extract_tool_call_ids, _snapshot_is_open_hitl_interrupt
        from agent.runtime import build_agent
        from permission import service as perm_svc
        from workspace.manager import get_session_dir

        session_id = str(session.id)
        session_dir = get_session_dir(current_user.workspace, session_id)
        agent = await build_agent(
            session_id=session_id,
            user_id=str(current_user.id),
            user_root=current_user.workspace,
            session_dir=session_dir,
            agent_id=session.agent_id,
            model_id=session.model_id,
        )
        snapshot = await agent.aget_state({"configurable": {"thread_id": session_id}})
        if not _snapshot_is_open_hitl_interrupt(snapshot):
            return _OpenHitlRecovery(action="none")

        tool_call_ids = _extract_tool_call_ids(snapshot)
        if not tool_call_ids:
            return _OpenHitlRecovery(action="blocked", reason="missing_tool_call_ids")

        permissions = []
        for tool_call_id in tool_call_ids:
            pr = await perm_svc.get_permission_request_by_tool_call(
                db,
                session.id,
                tool_call_id,
                statuses=["pending", "approved", "denied", "resumed", "auto_approved"],
            )
            if pr is None:
                return _OpenHitlRecovery(action="blocked", reason="missing_permission")
            permissions.append(pr)

        if any(pr.status == "pending" for pr in permissions):
            return _OpenHitlRecovery(action="waiting", reason="pending_permission")

        decisions: list[dict] = []
        for pr in permissions:
            if pr.status in {"approved", "resumed", "auto_approved"}:
                decisions.append({"type": "approve"})
            elif pr.status == "denied":
                decisions.append({
                    "type": "reject",
                    "message": "Permission denied by user",
                })
            else:
                return _OpenHitlRecovery(
                    action="blocked",
                    reason=f"unsupported_permission_status:{pr.status}",
                )

        return _OpenHitlRecovery(action="resume", decisions=decisions)
    except Exception:
        return _OpenHitlRecovery(action="none")


async def _recover_open_hitl_before_prompt(
    db: AsyncSession,
    session,
    current_user: User,
) -> dict | None:
    """Convert a stale error-state HITL checkpoint into a resume run."""
    if session.status != "error":
        return None

    recovery = await _inspect_open_hitl_recovery(db, session, current_user)
    if recovery.action == "none":
        return None

    if recovery.action == "waiting":
        await session_svc.update_session_status(db, session.id, "waiting")
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "PERMISSION_WAITING",
                "message": "Session is waiting for permission approval",
            },
        )

    if recovery.action != "resume" or not recovery.decisions:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CHECKPOINT_INTERRUPTED",
                "message": (
                    "Session checkpoint is waiting at a tool approval boundary "
                    "and cannot accept a new user message yet"
                ),
                "reason": recovery.reason,
            },
        )

    from agent.scheduler import enqueue_resume

    run = await enqueue_resume(db, session.id, recovery.decisions)
    await session_svc.update_session_status(db, session.id, "queued")
    await db.commit()

    return {
        "message_id": None,
        "run_id": str(run.id),
        "status": "queued",
        "recovered": True,
        "mode": "resume_open_hitl",
    }


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
        "subtask_waiting": "subtask_waiting",
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
    last_error = None
    retryable_model_continuation = False
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

        err_stmt = (
            sa_select(AgentRun)
            .where(AgentRun.session_id == session_id)
            .where(AgentRun.error.isnot(None))
            .order_by(AgentRun.updated_at.desc())
            .limit(1)
        )
        last_error_run = (await db.execute(err_stmt)).scalar_one_or_none()
        if last_error_run and last_error_run.error:
            last_error = last_error_run.error
            retryable_model_continuation = _diagnostics_allow_model_retry(
                last_error_run.diagnostics,
            )
            if (
                not retryable_model_continuation
                and _error_looks_like_provider_timeout(last_error)
            ):
                retryable_model_continuation = await _checkpoint_allows_model_retry(
                    session,
                    current_user,
                )
    except Exception:
        pass  # Graceful fallback — no diagnostics available yet

    if retryable_model_continuation:
        resumable = True

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

    # Phase P3: running detached tasks count
    running_detached_count = 0
    try:
        from agent.task_models import SessionTask
        from sqlalchemy import select as sa_select, func as sa_func
        import session.models as _sm  # noqa: F401

        count_stmt = (
            sa_select(sa_func.count())
            .select_from(SessionTask)
            .where(
                SessionTask.session_id == session_id,
                SessionTask.task_kind == "process",
                SessionTask.status == "running",
            )
        )
        result = await db.execute(count_stmt)
        val = result.scalar_one()
        running_detached_count = int(val) if val is not None else 0
    except Exception:
        running_detached_count = 0

    runtime = RuntimeResponse(
        session_id=session.id,
        status=session.status,
        phase=phase,
        last_message_seq=last_seq,
        pending_permissions_count=pending_count,
        resumable=resumable,
        last_error=last_error,
        updated_at=session.updated_at,
        last_call_prompt_tokens=ctx_prompt,
        last_call_completion_tokens=ctx_completion,
        context_window_limit=ctx_window,
        context_usage_ratio=ctx_ratio,
        last_compaction_at=last_compaction_at,
        compaction_count=compaction_count,
        has_running_detached_tasks=running_detached_count > 0,
        running_detached_tasks_count=running_detached_count,
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

    recovered = await _recover_open_hitl_before_prompt(db, session, current_user)
    if recovered is not None:
        return ok(recovered)

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


@router.post("/{session_id}/retry")
async def retry_session_model_continuation(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retry the model node from a recoverable checkpoint without a user message."""
    from agent.run_models import AgentRun
    from agent.scheduler import enqueue_continue
    from sqlalchemy import select as sa_select

    session = await session_svc.get_session(db, session_id)
    if not session or session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Session not found"},
        )

    if session.status in ("running", "waiting", "queued", "subtask_waiting"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CONFLICT",
                "message": f"Session is {session.status}; cannot retry now",
            },
        )

    err_stmt = (
        sa_select(AgentRun)
        .where(AgentRun.session_id == session_id)
        .where(AgentRun.status == "failed")
        .where(AgentRun.error.isnot(None))
        .order_by(AgentRun.updated_at.desc())
        .limit(1)
    )
    last_error_run = (await db.execute(err_stmt)).scalar_one_or_none()
    hitl_recovery = await _inspect_open_hitl_recovery(db, session, current_user)
    if hitl_recovery.action == "resume" and hitl_recovery.decisions:
        from agent.scheduler import enqueue_resume

        run = await enqueue_resume(db, session_id, hitl_recovery.decisions)
        await session_svc.update_session_status(db, session_id, "queued")
        await db.commit()
        return ok({
            "run_id": str(run.id),
            "status": "queued",
            "mode": "resume_open_hitl",
        })

    retryable = (
        bool(last_error_run)
        and (
            _diagnostics_allow_model_retry(last_error_run.diagnostics)
            or (
                _error_looks_like_provider_timeout(last_error_run.error)
                and await _checkpoint_allows_model_retry(session, current_user)
            )
        )
    )
    if not retryable:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "NOT_RETRYABLE",
                "message": "Latest failed run is not a retryable model continuation",
            },
        )

    run = await enqueue_continue(
        db,
        session_id,
        payload={"mode": "retry_model_node", "source_run_id": str(last_error_run.id)},
    )
    await session_svc.update_session_status(db, session_id, "queued")
    await db.commit()

    return ok({
        "run_id": str(run.id),
        "status": "queued",
        "mode": "retry_model_node",
    })


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
    from agent.scheduler import cancel_queued_runs, enqueue_abort, request_interrupt

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
    # Phase 7A: also set session-level interrupt flag so the running worker
    # sees the abort at its next tool boundary (cross-worker safe)
    await request_interrupt(db, session_id)
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
    from agent.scheduler import cancel_queued_runs, enqueue_abort, request_interrupt
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
            # Running: set interrupt flag + enqueue abort for the worker
            await request_interrupt(db, session_id)
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


# ── Task endpoints (Phase P3) ──────────────────────────────────────────────


@router.get("/{session_id}/tasks")
async def list_session_tasks(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all tasks (detached + child) for a session.

    Phase P3: Used by Task Output panel to reconcile task state on open,
    after run done, and on page refresh. Does not rely on SSE.
    """
    from agent.task_models import SessionTask
    from sqlalchemy import select
    import session.models  # noqa: F401
    import auth.models  # noqa: F401

    session = await session_svc.get_session(db, session_id)
    if not session or session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Session not found"},
        )

    stmt = (
        select(SessionTask)
        .where(SessionTask.session_id == session_id)
        .order_by(SessionTask.created_at.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()

    return ok([
        {
            "id": str(t.id),
            "task_kind": t.task_kind,
            "blocking_mode": t.blocking_mode,
            "status": t.status,
            "title": t.title,
            "command": t.command,
            "child_session_id": str(t.child_session_id) if t.child_session_id else None,
            "pid": t.pid,
            "artifact_root": t.artifact_root,
            "result_ref": t.result_ref,
            "error": t.error,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        }
        for t in rows
    ])


@router.get("/{session_id}/tasks/{task_id}")
async def get_session_task(
    session_id: uuid.UUID,
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get details for a single task."""
    from agent.task_models import SessionTask
    import session.models  # noqa: F401

    session = await session_svc.get_session(db, session_id)
    if not session or session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Session not found"},
        )

    task = await db.get(SessionTask, task_id)
    if not task or task.session_id != session_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Task not found"},
        )

    return ok({
        "id": str(task.id),
        "task_kind": task.task_kind,
        "blocking_mode": task.blocking_mode,
        "status": task.status,
        "title": task.title,
        "command": task.command,
        "child_session_id": str(task.child_session_id) if task.child_session_id else None,
        "pid": task.pid,
        "stdout_path": task.stdout_path,
        "stderr_path": task.stderr_path,
        "artifact_root": task.artifact_root,
        "result_ref": task.result_ref,
        "error": task.error,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    })


@router.get("/{session_id}/tasks/{task_id}/stdout")
async def get_task_stdout(
    session_id: uuid.UUID,
    task_id: uuid.UUID,
    tail: int = Query(100, ge=1, le=5000, description="Number of tail lines"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get the last N lines of a task's stdout log."""
    from agent.task_models import SessionTask
    from agent.tasks import read_task_stdout, read_task_stderr
    from workspace.manager import get_session_dir
    import session.models  # noqa: F401

    session = await session_svc.get_session(db, session_id)
    if not session or session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Session not found"},
        )

    task = await db.get(SessionTask, task_id)
    if not task or task.session_id != session_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Task not found"},
        )

    session_dir = get_session_dir(current_user.workspace, str(session_id))
    out_stdout = read_task_stdout(session_dir, str(task_id), tail_lines=tail).strip()
    out_stderr = read_task_stderr(session_dir, str(task_id), tail_lines=tail).strip()
    
    # Merge them. stderr usually contains progress, stdout contains final json.
    parts = []
    if out_stderr:
        parts.append(out_stderr)
    if out_stdout:
        parts.append(out_stdout)
        
    combined = "\n".join(parts)
    lines = combined.split("\n") if combined else []
    if len(lines) > tail:
        combined = "\n".join(lines[-tail:])

    return ok({"task_id": str(task_id), "lines": tail, "stdout": combined})


@router.post("/{session_id}/tasks/{task_id}/stop")
async def stop_session_task(
    session_id: uuid.UUID,
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Stop a running detached process task.

    Sends SIGTERM to the process and updates status to cancelled.
    Only applies to task_kind=process with status=running.
    """
    import signal
    from agent.task_models import SessionTask
    from agent.tasks import update_task_status
    from workspace.manager import get_session_dir
    import session.models  # noqa: F401

    session = await session_svc.get_session(db, session_id)
    if not session or session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Session not found"},
        )

    task = await db.get(SessionTask, task_id)
    if not task or task.session_id != session_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Task not found"},
        )

    if task.task_kind != "process":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "VALIDATION_ERROR", "message": "Only process tasks can be stopped"},
        )

    if task.status != "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CONFLICT", "message": f"Task is not running (status={task.status})"},
        )

    # Send SIGTERM to the process
    stopped = False
    if task.pid:
        try:
            import os as _os
            _os.kill(task.pid, signal.SIGTERM)
            stopped = True
        except ProcessLookupError:
            stopped = True  # Already dead
        except OSError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "INTERNAL_ERROR", "message": f"Failed to stop process: {e}"},
            )

    # Update DB status
    task.status = "cancelled"
    task.error = "stopped_by_user"
    await db.commit()

    # Update filesystem
    session_dir = get_session_dir(current_user.workspace, str(session_id))
    update_task_status(session_dir, str(task_id), "cancelled", error="stopped_by_user")

    return ok({
        "stopped": stopped,
        "task_id": str(task_id),
        "status": "cancelled",
        "pid": task.pid,
    })


# ── Panel submit endpoint (Phase P6-E / html_app) ─────────────────────────


@router.post("/{session_id}/panel-submit")
async def panel_submit(
    session_id: uuid.UUID,
    body: dict = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Receive user input from an html_app panel interaction.

    The frontend iframe posts user form data via postMessage → host → this API.
    The backend writes the response to a file that the detached task can poll,
    and publishes a panel_submit SSE event.

    Expected body:
    {
        "interaction_id": "...",
        "callback_task_id": "...",  // optional
        "data": { ... user form data ... }
    }
    """
    session = await session_svc.get_session(db, session_id)
    if not session or session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Session not found"},
        )

    interaction_id = body.get("interaction_id", "")
    callback_task_id = body.get("callback_task_id", "")
    data = body.get("data", {})

    if not interaction_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "VALIDATION_ERROR", "message": "interaction_id is required"},
        )

    # Write response to task artifact for detached process to consume
    if callback_task_id:
        from workspace.manager import get_session_dir
        session_dir = get_session_dir(current_user.workspace, str(session_id))
        _write_panel_response(session_dir, callback_task_id, interaction_id, data)

    # Publish SSE event so any listener can react
    from core.event_bridge import notify
    try:
        await notify(str(session_id), {
            "event": "panel_submit",
            "interaction_id": interaction_id,
            "callback_task_id": callback_task_id,
            "data": data,
        })
    except Exception:
        pass  # Best-effort SSE

    return ok({
        "received": True,
        "interaction_id": interaction_id,
        "callback_task_id": callback_task_id,
    })


def _write_panel_response(
    session_dir: str, task_id: str, interaction_id: str, data: dict,
) -> None:
    """Write panel response to task artifact directory.

    The detached process polls for this file to receive user input.
    """
    import json as _json

    task_dir = os.path.join(session_dir, ".agentd", "tasks", task_id)
    os.makedirs(task_dir, exist_ok=True)

    response_path = os.path.join(task_dir, "panel_response.json")
    with open(response_path, "w", encoding="utf-8") as f:
        _json.dump({
            "interaction_id": interaction_id,
            "data": data,
            "received_at": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat(),
        }, f, ensure_ascii=False, indent=2)
