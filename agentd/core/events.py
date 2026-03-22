"""EventBus — per-session asyncio.Queue for SSE event delivery.

Each active session gets its own Queue. The agent loop publishes events
via ``publish()``, and the SSE endpoint consumes them via ``subscribe()``.
"""

import asyncio
from datetime import datetime, timezone
from typing import Any


class EventBus:
    """Manages per-session event queues."""

    def __init__(self):
        self._queues: dict[str, asyncio.Queue] = {}

    def _get_or_create(self, session_id: str) -> asyncio.Queue:
        if session_id not in self._queues:
            self._queues[session_id] = asyncio.Queue()
        return self._queues[session_id]

    async def publish(self, session_id: str, event: dict[str, Any]) -> None:
        """Push an event into the session's queue.

        Automatically adds ``session_id`` and ``timestamp`` if missing.
        """
        event.setdefault("session_id", session_id)
        event.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        q = self._get_or_create(session_id)
        await q.put(event)

    async def subscribe(self, session_id: str) -> asyncio.Queue:
        """Return the queue for a session (creates if needed)."""
        return self._get_or_create(session_id)

    def remove(self, session_id: str) -> None:
        """Clean up the queue when a session's SSE connection closes."""
        self._queues.pop(session_id, None)


# Module-level singleton
event_bus = EventBus()
