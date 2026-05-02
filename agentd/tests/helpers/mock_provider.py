"""Mock provider failure helpers for v0.4.4 runtime regression tests."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


def closed_tool_result_snapshot(*, next_nodes=("model",), interrupts=()):
    return SimpleNamespace(
        values={"messages": closed_tool_result_messages()},
        next=next_nodes,
        interrupts=interrupts,
    )


def closed_tool_result_messages() -> list:
    return [
        HumanMessage(content="run a tool"),
        AIMessage(
            content="",
            tool_calls=[{
                "id": "call_1",
                "name": "bash",
                "args": {"command": "printf ok"},
            }],
        ),
        ToolMessage(content="ok", tool_call_id="call_1", name="bash"),
    ]


def open_hitl_snapshot():
    return SimpleNamespace(
        values={"messages": [
            HumanMessage(content="write a file"),
            AIMessage(
                content="",
                tool_calls=[{
                    "id": "call_write",
                    "name": "file_write",
                    "args": {"path": "x.txt", "content": "x"},
                }],
            ),
        ]},
        next=("HumanInTheLoopMiddleware.after_model",),
        interrupts=[SimpleNamespace(value={
            "action_requests": [{
                "name": "file_write",
                "args": {"path": "x.txt", "content": "x"},
            }],
            "tool_call_ids": ["call_write"],
        })],
    )


def invalid_provider_payload_messages() -> list[dict]:
    return [
        {"role": "user", "content": "run a tool"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "bash", "arguments": "{\"command\":\"ls\"}"},
            }],
        },
    ]


def valid_provider_payload_after_tool_result() -> list[dict]:
    return [
        {"role": "user", "content": "run a tool"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "bash", "arguments": "{\"command\":\"ls\"}"},
            }],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
    ]


def read_timeout() -> httpx.ReadTimeout:
    request = httpx.Request("POST", "https://provider.example/v1/chat/completions")
    return httpx.ReadTimeout("provider timed out after tool_result", request=request)


def connect_error() -> httpx.ConnectError:
    request = httpx.Request("POST", "https://provider.example/v1/chat/completions")
    return httpx.ConnectError("provider connection failed after tool_result", request=request)


def protocol_400_error() -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://provider.example/v1/chat/completions")
    response = httpx.Response(
        400,
        request=request,
        json={"error": {"message": "invalid tool message adjacency"}},
    )
    return httpx.HTTPStatusError("400 Bad Request", request=request, response=response)
