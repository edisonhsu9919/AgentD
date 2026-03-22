import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from session.models import Message, Session


# ── Session CRUD ─────────────────────────────────────────────────────────────


async def create_session(
    db: AsyncSession,
    user_id: uuid.UUID,
    model_id: str,
    title: str = "New Session",
    agent_id: str = "build",
    parent_id: Optional[uuid.UUID] = None,
) -> Session:
    session = Session(
        id=uuid.uuid4(),
        user_id=user_id,
        title=title,
        agent_id=agent_id,
        model_id=model_id,
        parent_id=parent_id,
        status="idle",
        token_usage={"input": 0, "output": 0, "total": 0},
    )
    db.add(session)
    await db.flush()
    return session


async def list_sessions(
    db: AsyncSession,
    user_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Session], int]:
    """Return (sessions, total) for a user, ordered by updated_at desc."""
    # Count
    count_q = select(func.count()).select_from(Session).where(Session.user_id == user_id)
    total = (await db.execute(count_q)).scalar_one()

    # Fetch
    q = (
        select(Session)
        .where(Session.user_id == user_id)
        .order_by(Session.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(q)).scalars().all()
    return list(rows), total


async def get_session(
    db: AsyncSession, session_id: uuid.UUID
) -> Optional[Session]:
    result = await db.execute(select(Session).where(Session.id == session_id))
    return result.scalar_one_or_none()


async def delete_session(db: AsyncSession, session_id: uuid.UUID) -> bool:
    """Delete a session and all its messages (CASCADE). Returns True if found."""
    result = await db.execute(delete(Session).where(Session.id == session_id))
    return result.rowcount > 0


async def update_session_status(
    db: AsyncSession, session_id: uuid.UUID, status: str
) -> None:
    await db.execute(
        update(Session)
        .where(Session.id == session_id)
        .values(status=status, updated_at=datetime.now(timezone.utc))
    )


async def update_token_usage(
    db: AsyncSession, session_id: uuid.UUID, token_usage: dict[str, int]
) -> None:
    await db.execute(
        update(Session)
        .where(Session.id == session_id)
        .values(token_usage=token_usage, updated_at=datetime.now(timezone.utc))
    )


async def update_loaded_skills(
    db: AsyncSession, session_id: uuid.UUID, loaded_skills: list[dict[str, str]]
) -> None:
    """Update the list of loaded skills for a session.

    Each entry is ``{"name": "...", "version": "..."}``.
    """
    await db.execute(
        update(Session)
        .where(Session.id == session_id)
        .values(loaded_skills=loaded_skills, updated_at=datetime.now(timezone.utc))
    )


# ── Message CRUD ─────────────────────────────────────────────────────────────


async def _next_seq(db: AsyncSession, session_id: uuid.UUID) -> int:
    """Return the next message sequence number for a session."""
    result = await db.execute(
        select(func.coalesce(func.max(Message.seq), 0))
        .where(Message.session_id == session_id)
    )
    return result.scalar_one() + 1


async def create_message(
    db: AsyncSession,
    session_id: uuid.UUID,
    role: str,
    parts: list[dict[str, Any]],
    is_summary: bool = False,
    token_usage: Optional[dict[str, Any]] = None,
) -> Message:
    seq = await _next_seq(db, session_id)
    msg = Message(
        id=uuid.uuid4(),
        session_id=session_id,
        role=role,
        parts=parts,
        is_summary=is_summary,
        token_usage=token_usage,
        seq=seq,
    )
    db.add(msg)
    await db.flush()

    # Touch session updated_at
    await db.execute(
        update(Session)
        .where(Session.id == session_id)
        .values(updated_at=datetime.now(timezone.utc))
    )
    return msg


async def list_messages(
    db: AsyncSession, session_id: uuid.UUID
) -> list[Message]:
    """Return all messages for a session, ordered by seq."""
    q = (
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.seq.asc())
    )
    rows = (await db.execute(q)).scalars().all()
    return list(rows)


async def count_messages(db: AsyncSession, session_id: uuid.UUID) -> int:
    """Return the total number of messages for a session."""
    result = await db.execute(
        select(func.count()).select_from(Message).where(Message.session_id == session_id)
    )
    return result.scalar_one()


async def get_last_message_seq(db: AsyncSession, session_id: uuid.UUID) -> int:
    """Return the highest message seq for a session, or 0 if none."""
    result = await db.execute(
        select(func.coalesce(func.max(Message.seq), 0))
        .where(Message.session_id == session_id)
    )
    return result.scalar_one()


async def append_part(
    db: AsyncSession, message_id: uuid.UUID, part: dict[str, Any]
) -> None:
    """Append a new part to an existing message's parts JSONB array."""
    result = await db.execute(select(Message).where(Message.id == message_id))
    msg = result.scalar_one_or_none()
    if msg is None:
        return
    current_parts = list(msg.parts) if msg.parts else []
    current_parts.append(part)
    await db.execute(
        update(Message)
        .where(Message.id == message_id)
        .values(parts=current_parts)
    )
