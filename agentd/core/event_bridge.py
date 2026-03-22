"""PG LISTEN/NOTIFY event bridge — cross-process SSE delivery.

Worker processes call ``notify(session_id, event)`` which issues a
PostgreSQL NOTIFY on channel ``agentd_events``.

The API process starts a ``Listener`` background task that LISTENs on
the same channel and dispatches received events into the local
``event_bus`` queues for SSE delivery.

This keeps PostgreSQL as the single truth center (no Redis needed).
Payload limit: PG NOTIFY supports up to 8000 bytes per message.
"""

import asyncio
import json
from typing import Any

import psycopg
from psycopg import AsyncConnection, sql

from core.config import settings

CHANNEL = "agentd_events"


# ── Publisher (used by workers) ──────────────────────────────────────────


async def notify(session_id: str, event: dict[str, Any]) -> None:
    """Send an event via PG NOTIFY. Called from worker processes.

    Opens a short-lived connection per call. For high-throughput scenarios
    a persistent connection could be reused, but for Phase C v1 this is
    sufficient (worker typically sends ~10-50 events per run).
    """
    from datetime import datetime, timezone

    event.setdefault("session_id", session_id)
    event.setdefault("timestamp", datetime.now(timezone.utc).isoformat())

    payload = json.dumps(event, default=str)

    # Truncate if payload exceeds PG NOTIFY limit (~8000 bytes)
    if len(payload) > 7500:
        # Send a truncated marker instead — frontend can recover via DB
        payload = json.dumps({
            "session_id": session_id,
            "event": event.get("event", "unknown"),
            "truncated": True,
            "timestamp": event["timestamp"],
        }, default=str)

    conninfo = settings.checkpoint_db_url  # psycopg3-compatible URL
    try:
        async with await AsyncConnection.connect(conninfo, autocommit=True) as conn:
            # psycopg3 does not support parameterized NOTIFY ($1 is invalid).
            # Use sql.SQL + sql.Literal to safely quote the payload string.
            await conn.execute(
                sql.SQL("NOTIFY {}, {}").format(
                    sql.Identifier(CHANNEL),
                    sql.Literal(payload),
                )
            )
    except Exception as e:
        if settings.debug:
            print(f"[event_bridge] notify FAILED for session={session_id}: {type(e).__name__}: {e}")
        raise  # Re-raise so caller (_publish) sees the failure


# ── Listener (used by API process) ───────────────────────────────────────


class EventBridgeListener:
    """Background task that LISTENs on PG channel and dispatches to event_bus."""

    def __init__(self):
        self._task: asyncio.Task | None = None
        self._conn: AsyncConnection | None = None

    async def start(self) -> None:
        """Start the listener background task."""
        if self._task and not self._task.done():
            return  # Already running
        self._task = asyncio.create_task(self._listen_loop())

    async def stop(self) -> None:
        """Stop the listener."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def _listen_loop(self) -> None:
        """Connect and listen for notifications, dispatching to event_bus."""
        from core.events import event_bus

        conninfo = settings.checkpoint_db_url

        while True:
            try:
                self._conn = await AsyncConnection.connect(
                    conninfo, autocommit=True,
                )
                await self._conn.execute(f"LISTEN {CHANNEL}")

                if settings.debug:
                    print(f"[event_bridge] Listening on channel '{CHANNEL}'")

                async for notify_msg in self._conn.notifies():
                    try:
                        event = json.loads(notify_msg.payload)
                        session_id = event.get("session_id", "")
                        if settings.debug:
                            print(f"[event_bridge] Received: event={event.get('event', '?')} session={session_id[:12] if session_id else '?'}")
                        if session_id:
                            await event_bus.publish(session_id, event)
                    except json.JSONDecodeError:
                        if settings.debug:
                            print(f"[event_bridge] Bad JSON payload: {notify_msg.payload[:200]}")
                    except Exception:
                        if settings.debug:
                            import traceback
                            traceback.print_exc()

            except asyncio.CancelledError:
                raise
            except Exception:
                if settings.debug:
                    import traceback
                    print("[event_bridge] Connection lost, reconnecting in 2s...")
                    traceback.print_exc()
                await asyncio.sleep(2)
            finally:
                if self._conn and not self._conn.closed:
                    await self._conn.close()
                    self._conn = None


# Module-level singleton
listener = EventBridgeListener()
