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
        self._subscribers: dict[str, set[asyncio.Queue]] = {}

    @property
    def _queues(self) -> dict[str, asyncio.Queue]:
        """Backward-compatible view for older tests/debug code.

        New code should treat EventBus as a broadcast bus. This property exposes
        one representative queue per session so legacy introspection does not
        break while preventing new code from relying on a single shared queue.
        """
        return {
            session_id: next(iter(queues))
            for session_id, queues in self._subscribers.items()
            if queues
        }

    async def publish(self, session_id: str, event: dict[str, Any]) -> None:
        """Push an event into the session's queue.

        Automatically adds ``session_id`` and ``timestamp`` if missing.
        """
        event.setdefault("session_id", session_id)
        event.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        for q in list(self._subscribers.get(session_id, set())):
            await q.put(dict(event))

    async def subscribe(self, session_id: str) -> asyncio.Queue:
        """Return a dedicated subscriber queue for a session."""
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(session_id, set()).add(q)
        return q

    def remove(self, session_id: str, queue: asyncio.Queue | None = None) -> None:
        """Clean up a subscriber queue when an SSE connection closes."""
        if queue is None:
            self._subscribers.pop(session_id, None)
            return
        subscribers = self._subscribers.get(session_id)
        if not subscribers:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(session_id, None)


# Module-level singleton
event_bus = EventBus()
