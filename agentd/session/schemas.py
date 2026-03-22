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
    """

    session_id: uuid.UUID
    status: str  # idle | queued | running | waiting | error
    phase: Optional[str] = None  # queued | running | permission_waiting | error | None
    last_message_seq: int
    pending_permissions_count: int
    resumable: bool
    last_error: Optional[str] = None
    updated_at: datetime
