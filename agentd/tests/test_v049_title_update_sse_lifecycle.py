"""v0.4.9 title update SSE lifecycle tests."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_session_sse_does_not_close_after_done_before_title_update():
    from session.router import _session_event_generator

    queue: asyncio.Queue = asyncio.Queue()
    await queue.put({"event": "done", "token_usage": {"total": 10}})
    await queue.put({"event": "title_update", "title": "自动标题"})

    request = MagicMock()
    request.is_disconnected = AsyncMock(return_value=False)
    event_bus = MagicMock()

    generator = _session_event_generator(
        session_id="session-1",
        request=request,
        queue=queue,
        event_bus=event_bus,
    )
    try:
        first = await anext(generator)
        second = await anext(generator)
    finally:
        await generator.aclose()

    assert first["event"] == "done"
    assert json.loads(first["data"])["event"] == "done"
    assert second["event"] == "title_update"
    assert json.loads(second["data"])["title"] == "自动标题"
    event_bus.remove.assert_called_once_with("session-1", queue)


@pytest.mark.asyncio
async def test_event_bus_broadcasts_to_multiple_sse_subscribers():
    from core.events import EventBus

    bus = EventBus()
    q1 = await bus.subscribe("session-1")
    q2 = await bus.subscribe("session-1")

    await bus.publish("session-1", {"event": "title_update", "title": "自动标题"})

    assert (await q1.get())["event"] == "title_update"
    assert (await q2.get())["title"] == "自动标题"

    bus.remove("session-1", q1)
    await bus.publish("session-1", {"event": "done"})
    assert (await q2.get())["event"] == "done"
