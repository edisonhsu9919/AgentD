import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from session.models import Message, Session

BUSY_DELETE_STATUSES = {"queued", "running", "waiting", "subtask_waiting"}


@dataclass(frozen=True)
class DeleteSessionTreeResult:
    deleted_session_ids: list[uuid.UUID]

    @property
    def deleted_count(self) -> int:
        return len(self.deleted_session_ids)


class SessionTreeBusyError(Exception):
    def __init__(self, blocking_session_ids: list[uuid.UUID]):
        self.blocking_session_ids = blocking_session_ids
        super().__init__("Session or child sessions are still running")


class SessionTreeOwnershipError(Exception):
    pass


def normalize_agent_id(agent_id: str | None) -> str:
    """Normalize legacy agent ids to the canonical runtime identity."""
    normalized = (agent_id or "").strip()
    if not normalized or normalized == "build":
        return "assistant"
    return normalized


# ── Session CRUD ─────────────────────────────────────────────────────────────


async def create_session(
    db: AsyncSession,
    user_id: uuid.UUID,
    model_id: str,
    title: str = "New Session",
    agent_id: str = "assistant",
    parent_id: Optional[uuid.UUID] = None,
    is_internal: bool = False,
) -> Session:
    resolved_agent_id = normalize_agent_id(agent_id)
    session = Session(
        id=uuid.uuid4(),
        user_id=user_id,
        title=title,
        agent_id=resolved_agent_id,
        model_id=model_id,
        parent_id=parent_id,
        is_internal=is_internal,
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
    """Return (sessions, total) for a user, ordered by updated_at desc.

    Child sessions (parent_id != NULL) are excluded from the list —
    they are internal implementation details of launch_subagent and
    should not appear in the sidebar.
    """
    # Count — exclude child sessions
    count_q = (
        select(func.count())
        .select_from(Session)
        .where(
            Session.user_id == user_id,
            Session.parent_id.is_(None),
            Session.is_internal.is_(False),
        )
    )
    total = (await db.execute(count_q)).scalar_one()

    # Fetch — exclude child sessions
    q = (
        select(Session)
        .where(
            Session.user_id == user_id,
            Session.parent_id.is_(None),
            Session.is_internal.is_(False),
        )
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


async def update_session_title(
    db: AsyncSession,
    session_id: uuid.UUID,
    title: str,
) -> Optional[Session]:
    from agent.session_title import sanitize_session_title

    normalized = sanitize_session_title(title, max_chars=80)
    if not normalized:
        return None
    await db.execute(
        update(Session)
        .where(Session.id == session_id)
        .values(title=normalized, updated_at=datetime.now(timezone.utc))
    )
    await db.flush()
    return await get_session(db, session_id)


async def delete_session(db: AsyncSession, session_id: uuid.UUID) -> bool:
    """Delete one session row.

    Prefer ``delete_session_tree`` for API paths so child sessions are handled
    intentionally. This low-level helper remains for backward compatibility.
    """
    return await _delete_session_row(db, session_id) > 0


async def _delete_session_row(db: AsyncSession, session_id: uuid.UUID) -> int:
    result = await db.execute(delete(Session).where(Session.id == session_id))
    return result.rowcount or 0


async def collect_session_tree(
    db: AsyncSession,
    root_session_id: uuid.UUID,
) -> list[Session]:
    """Return root + descendants in parent-before-child order."""
    root = await get_session(db, root_session_id)
    if not root:
        return []

    tree: list[Session] = [root]
    seen = {root.id}
    frontier = [root.id]

    while frontier:
        rows = (
            await db.execute(select(Session).where(Session.parent_id.in_(frontier)))
        ).scalars().all()
        next_frontier: list[uuid.UUID] = []
        for session in rows:
            if session.id in seen:
                continue
            seen.add(session.id)
            tree.append(session)
            next_frontier.append(session.id)
        frontier = next_frontier

    return tree


async def delete_session_tree(
    db: AsyncSession,
    root_session_id: uuid.UUID,
    user_id: uuid.UUID,
) -> DeleteSessionTreeResult:
    """Delete a session and all descendant child sessions.

    The delete order is leaf-to-root so the self-referential parent_id FK does
    not block deletion. Associated rows with session_id FKs are handled by DB
    cascades.
    """
    tree = await collect_session_tree(db, root_session_id)
    if not tree:
        return DeleteSessionTreeResult(deleted_session_ids=[])

    if any(session.user_id != user_id for session in tree):
        raise SessionTreeOwnershipError(
            "Session tree contains sessions owned by another user"
        )

    blocking = [
        session.id for session in tree
        if session.status in BUSY_DELETE_STATUSES
    ]
    if blocking:
        raise SessionTreeBusyError(blocking)

    deleted_ids = [session.id for session in tree]
    for session in reversed(tree):
        await _delete_session_row(db, session.id)

    return DeleteSessionTreeResult(deleted_session_ids=deleted_ids)


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
