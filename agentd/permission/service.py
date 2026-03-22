import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from permission.models import PermissionRequest


async def create_permission_request(
    db: AsyncSession,
    session_id: uuid.UUID,
    tool_call_id: str,
    tool_name: str,
    tool_input: dict,
    permission_id: uuid.UUID | None = None,
) -> PermissionRequest:
    """Create a new permission request record (status=pending)."""
    pr = PermissionRequest(
        id=permission_id or uuid.uuid4(),
        session_id=session_id,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        input=tool_input,
        status="pending",
    )
    db.add(pr)
    await db.flush()
    return pr


async def get_permission_request(
    db: AsyncSession, permission_id: uuid.UUID
) -> Optional[PermissionRequest]:
    result = await db.execute(
        select(PermissionRequest).where(PermissionRequest.id == permission_id)
    )
    return result.scalar_one_or_none()


async def resolve_permission(
    db: AsyncSession,
    permission_id: uuid.UUID,
    decision: str,  # "approved" or "denied"
) -> bool:
    """Update permission status and resolved_at. Returns True if found and was pending."""
    result = await db.execute(
        update(PermissionRequest)
        .where(
            PermissionRequest.id == permission_id,
            PermissionRequest.status == "pending",
        )
        .values(status=decision, resolved_at=datetime.now(timezone.utc))
    )
    return result.rowcount > 0


async def count_pending_by_session(
    db: AsyncSession, session_id: uuid.UUID
) -> int:
    """Count pending permission requests for a session."""
    result = await db.execute(
        select(func.count())
        .select_from(PermissionRequest)
        .where(
            PermissionRequest.session_id == session_id,
            PermissionRequest.status == "pending",
        )
    )
    return result.scalar_one()


async def get_pending_by_session(
    db: AsyncSession, session_id: uuid.UUID
) -> list[PermissionRequest]:
    """Get all pending permission requests for a session, ordered by creation time."""
    result = await db.execute(
        select(PermissionRequest)
        .where(
            PermissionRequest.session_id == session_id,
            PermissionRequest.status == "pending",
        )
        .order_by(PermissionRequest.created_at)
    )
    return list(result.scalars().all())


async def cancel_pending_by_session(
    db: AsyncSession, session_id: uuid.UUID
) -> int:
    """Cancel all pending permission requests for a session (used by abort).

    Sets status to 'cancelled' and resolved_at to now. Returns the count of
    cancelled records.
    """
    result = await db.execute(
        update(PermissionRequest)
        .where(
            PermissionRequest.session_id == session_id,
            PermissionRequest.status == "pending",
        )
        .values(status="cancelled", resolved_at=datetime.now(timezone.utc))
    )
    return result.rowcount


async def get_resolved_by_session(
    db: AsyncSession, session_id: uuid.UUID
) -> list[PermissionRequest]:
    """Get recently resolved (approved/denied) permission requests for a session.

    Used by Phase C permission router to build batch decisions for resume.
    Orders by creation time to preserve the original interrupt order.
    Only returns permissions that have NOT yet been consumed by a resume run
    (status must be exactly "approved" or "denied", not "resumed").
    """
    result = await db.execute(
        select(PermissionRequest)
        .where(
            PermissionRequest.session_id == session_id,
            PermissionRequest.status.in_(["approved", "denied"]),
        )
        .order_by(PermissionRequest.created_at)
    )
    return list(result.scalars().all())


async def mark_resolved_as_resumed(
    db: AsyncSession, session_id: uuid.UUID
) -> int:
    """Mark all approved/denied permissions as 'resumed' after resume is enqueued.

    This prevents them from being double-counted in future interrupt cycles.
    Returns the count of updated records.
    """
    result = await db.execute(
        update(PermissionRequest)
        .where(
            PermissionRequest.session_id == session_id,
            PermissionRequest.status.in_(["approved", "denied"]),
        )
        .values(status="resumed")
    )
    return result.rowcount
