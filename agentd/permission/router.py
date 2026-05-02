"""Permission router — approve/deny endpoints for human-in-the-loop (§5.3, §8.2).

Each approve/deny resolves one permission_request in the DB. When ALL pending
permissions for the session are resolved, the router enqueues a 'resume' run
via ``scheduler.enqueue_resume()`` (Phase C).

Phase B adds `mode: "once" | "always"` to approve. When "always" is chosen,
the tool call is written as a rule into the session policy file.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user
from auth.models import User
from core.database import get_db
from core.events import event_bus
from core.response import ok
from permission import service as perm_svc
from session import service as session_svc

router = APIRouter()


# ── Request schema ──────────────────────────────────────────────────────────


class ApproveRequest(BaseModel):
    """Optional body for approve endpoint (Phase B)."""
    mode: str = "once"  # "once" | "always"


# ── Core resolve logic ──────────────────────────────────────────────────────


def _build_policy_rule(tool_name: str, tool_input: dict):
    """Build a PolicyRule from a tool call for approve-always (Phase B)."""
    from permission.policy import PolicyRule

    if tool_name == "bash":
        command = tool_input.get("command", "")
        if command:
            return PolicyRule(
                tool="bash",
                effect="allow",
                match={"kind": "exact_command", "command": command.strip()},
            )

    if tool_name in ("file_write", "file_edit"):
        return PolicyRule(
            tool=tool_name,
            effect="allow",
            match={"kind": "any_path_within_session"},
        )

    # Other tools: no approve-always rule yet
    return None


async def _current_open_hitl_tool_call_ids(session, current_user: User) -> list[str]:
    try:
        from agent.executor import _extract_tool_call_ids
        from agent.runtime import build_agent
        from workspace.manager import get_session_dir

        session_id = str(session.id)
        session_dir = get_session_dir(current_user.workspace, session_id)
        agent = await build_agent(
            session_id=session_id,
            user_id=str(session.user_id),
            user_root=current_user.workspace,
            session_dir=session_dir,
            agent_id=session.agent_id,
            model_id=session.model_id,
        )
        snapshot = await agent.aget_state({"configurable": {"thread_id": session_id}})
        return _extract_tool_call_ids(snapshot) if snapshot else []
    except Exception:
        return []


async def _resolve_and_maybe_resume(
    db: AsyncSession,
    permission_id: uuid.UUID,
    decision: str,  # "approved" or "denied"
    current_user: User,
    approve_mode: str = "once",
) -> dict:
    """Resolve a single permission and enqueue resume if all pending are resolved."""
    from workspace.manager import get_session_dir

    pr = await perm_svc.get_permission_request(db, permission_id)
    if not pr:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Permission request not found"},
        )

    # Ownership check
    session = await session_svc.get_session(db, pr.session_id)
    if not session or session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Permission request not found"},
        )

    if pr.status != "pending":
        # Idempotent: if already resolved with the same decision, return success
        # This prevents duplicate approve clicks from causing errors
        if pr.status == decision:
            return {"permission_id": str(permission_id), "decision": decision}
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CONFLICT", "message": f"Permission already {pr.status}"},
        )

    # Resolve this permission in DB (atomic: WHERE status=pending).
    # DO NOT commit yet — the resolve and (potential) resume enqueue must
    # land in a single transaction to prevent the half-committed dead state
    # where permission is "approved" but no resume run is enqueued (#P1).
    was_resolved = await perm_svc.resolve_permission(db, permission_id, decision)

    # Race guard: if another concurrent request already resolved this permission,
    # return idempotent success without enqueuing a duplicate resume.
    # No need to commit — the UPDATE matched 0 rows so there's nothing to persist.
    if not was_resolved:
        return {"permission_id": str(permission_id), "decision": decision}

    # Phase B: if approve-always, write rule to session policy (filesystem, not DB)
    if decision == "approved" and approve_mode == "always":
        try:
            from permission.policy import load_policy, save_policy, add_rule

            session_dir = get_session_dir(current_user.workspace, str(pr.session_id))
            policy = load_policy(session_dir)
            rule = _build_policy_rule(pr.tool_name, pr.input)
            if rule:
                policy = add_rule(policy, rule)
                save_policy(session_dir, policy)
        except Exception:
            pass  # best-effort: don't block the approve flow

    # Check if all pending permissions for this session are now resolved.
    # This query sees our uncommitted UPDATE because it's in the same transaction.
    remaining = await perm_svc.count_pending_by_session(db, pr.session_id)

    if remaining == 0:
        # All resolved — build batch decisions and enqueue a resume run
        from agent.scheduler import enqueue_resume

        # Query only the current interrupt batch. Historical approved/denied
        # rows must not leak into this resume Command.
        current_tool_call_ids = await _current_open_hitl_tool_call_ids(session, current_user)
        all_resolved = (
            await perm_svc.get_resolved_by_tool_call_ids(
                db,
                pr.session_id,
                current_tool_call_ids,
            )
            if current_tool_call_ids
            else await perm_svc.get_resolved_by_session(db, pr.session_id)
        )
        decisions_batch: list[dict] = []
        for rpr in all_resolved:
            if rpr.status == "approved":
                decisions_batch.append({"type": "approve"})
            else:
                decisions_batch.append({
                    "type": "reject",
                    "message": "Permission denied by user",
                })

        if not decisions_batch:
            # Fallback: single decision
            if decision == "approved":
                decisions_batch = [{"type": "approve"}]
            else:
                decisions_batch = [{"type": "reject", "message": "Permission denied by user"}]

        await enqueue_resume(db, pr.session_id, decisions_batch)
        # Do not mark permissions as resumed here. The resume worker owns
        # consuming approved/denied HITL decisions after it has written the
        # matching tool_result, so a failed worker cannot lose the evidence
        # needed to close the tool-call group.

    # SINGLE atomic commit — resolves permission + enqueues resume (if applicable).
    # If this fails, everything rolls back: permission stays "pending", no orphaned state.
    await db.commit()

    # SSE events AFTER commit so frontend sees consistent state on poll
    await event_bus.publish(str(pr.session_id), {
        "event": "permission_resolved",
        "permission_id": str(permission_id),
        "decision": decision,
    })

    return {"permission_id": str(permission_id), "decision": decision}


# ── Endpoints ───────────────────────────────────────────────────────────────


@router.post("/{permission_id}/approve")
async def approve_permission(
    permission_id: uuid.UUID,
    body: Optional[ApproveRequest] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Approve a pending permission request.

    Optional body: {"mode": "once"} (default) or {"mode": "always"}.
    When mode is "always", the tool call is saved as a session policy rule
    so future identical calls are auto-approved (Phase B).
    """
    approve_mode = (body.mode if body else "once")
    result = await _resolve_and_maybe_resume(
        db, permission_id, "approved", current_user, approve_mode=approve_mode,
    )
    return ok(result)


@router.post("/{permission_id}/deny")
async def deny_permission(
    permission_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Deny a pending permission request."""
    result = await _resolve_and_maybe_resume(
        db, permission_id, "denied", current_user,
    )
    return ok(result)
