import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    id: uuid.UUID
    username: str
    role: str
    workspace: str
    is_active: bool
    department: str = ""
    employee_id: str = ""
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


# ── User profile (Phase H1) ─────────────────────────────────────────────────


class UserSkillItem(BaseModel):
    """Single skill entry in the user profile."""
    name: str
    version: str
    is_enabled: bool = True
    usage_count: int = 0
    last_used_at: Optional[datetime] = None
    icon: str = ""

    model_config = {"from_attributes": True}


class UserProfileResponse(BaseModel):
    """Full user profile with installed skills."""
    id: uuid.UUID
    username: str
    role: str
    workspace: str
    is_active: bool
    department: str = ""
    employee_id: str = ""
    created_at: datetime
    installed_skills: list[UserSkillItem] = Field(default_factory=list)

    model_config = {"from_attributes": True}


# ── Admin user management (Phase C.5, enhanced Phase H2) ────────────────────


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=6)
    role: str = Field(default="user", pattern=r"^(user|admin)$")
    is_active: bool = True
    department: str = Field(default="", max_length=128)
    employee_id: str = Field(default="", max_length=64)


class UpdateUserRequest(BaseModel):
    role: Optional[str] = Field(default=None, pattern=r"^(user|admin)$")
    is_active: Optional[bool] = None
    password: Optional[str] = Field(default=None, min_length=6)
    department: Optional[str] = Field(default=None, max_length=128)
    employee_id: Optional[str] = Field(default=None, max_length=64)


class UserSkillToggleRequest(BaseModel):
    """Admin request to enable/disable a user's skill."""
    is_enabled: bool
