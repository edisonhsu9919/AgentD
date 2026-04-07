import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Request ──────────────────────────────────────────────────────────────────


class SessionCreate(BaseModel):
    title: str = "New Session"
    agent_id: str = "build"
    model_id: Optional[str] = None


class PromptRequest(BaseModel):
    content: str
    attachments: Optional[list[dict[str, Any]]] = None


# ── Response ─────────────────────────────────────────────────────────────────


class TokenUsageResponse(BaseModel):
    input: int = 0
    output: int = 0
    total: int = 0


class SessionResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    title: str
    agent_id: str
    model_id: str
    parent_id: Optional[uuid.UUID] = None
    status: str
    token_usage: TokenUsageResponse
    loaded_skills: list[dict[str, str]] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MessageResponse(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    role: str
    parts: list[dict[str, Any]]
    is_summary: bool
    token_usage: Optional[dict[str, Any]] = None
    seq: int
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Runtime recovery (Phase A) ──────────────────────────────────────────────


class RuntimeResponse(BaseModel):
    """Session runtime snapshot for frontend state recovery.

    Derived from existing tables (sessions, messages, permission_requests)
    — no new DB table required.

    Phase L: includes current-round context occupancy from the latest
    model response, enabling frontend "Prompt 3181 / 32768" display.
    """

    session_id: uuid.UUID
    status: str  # idle | queued | running | waiting | error
    phase: Optional[str] = None  # queued | running | permission_waiting | error | None
    last_message_seq: int
    pending_permissions_count: int
    resumable: bool
    last_error: Optional[str] = None
    updated_at: datetime
    # Context occupancy — from the latest model call's usage_metadata.
    # NOTE: These values reflect the LAST COMPLETED model call, not real-time state.
    # After compaction, the ratio will still show the pre-compaction value until the
    # next model call completes. Frontend should use compaction_count or
    # last_compaction_at to infer that a compaction just happened.
    last_call_prompt_tokens: int = 0
    last_call_completion_tokens: int = 0
    context_window_limit: Optional[int] = None
    context_usage_ratio: Optional[float] = None
    # Phase N1: compaction state
    last_compaction_at: Optional[datetime] = None
    compaction_count: int = 0
    # Phase P3: running detached tasks indicator
    has_running_detached_tasks: bool = False
    running_detached_tasks_count: int = 0
