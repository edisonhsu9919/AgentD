"""Permission request schemas (Phase A — state recovery)."""

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class PendingPermissionResponse(BaseModel):
    """Response schema for a pending permission request."""

    id: uuid.UUID
    session_id: uuid.UUID
    tool_call_id: Optional[str] = None
    tool_name: str
    input: dict[str, Any]
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}
