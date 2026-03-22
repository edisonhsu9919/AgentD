import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Request ──────────────────────────────────────────────────────────────────


class SkillCreate(BaseModel):
    name: str = Field(..., max_length=128)
    description: str
    content: str
    version: str = Field(default="0.1.0", max_length=32)
    license: str = Field(default="", max_length=128)
    compatibility: str = Field(default="", max_length=128)
    icon: str = Field(default="", max_length=256)
    metadata_extra: dict[str, Any] = Field(default_factory=dict)
    source_type: str = Field(default="manual", max_length=32)
    source_path: Optional[str] = None
    tags: list[str] = Field(default_factory=list)


class SkillUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=128)
    description: Optional[str] = None
    content: Optional[str] = None
    version: Optional[str] = Field(None, max_length=32)
    license: Optional[str] = Field(None, max_length=128)
    compatibility: Optional[str] = Field(None, max_length=128)
    icon: Optional[str] = Field(None, max_length=256)
    metadata_extra: Optional[dict[str, Any]] = None
    tags: Optional[list[str]] = None
    is_active: Optional[bool] = None


class SkillImportLocal(BaseModel):
    source_path: str = Field(..., description="Absolute path to the local skill package directory")


# ── Response ─────────────────────────────────────────────────────────────────


class SkillSummaryResponse(BaseModel):
    """Listing response — excludes content full text (§5.4)."""

    id: uuid.UUID
    name: str
    description: str
    version: str
    license: str = ""
    compatibility: str = ""
    icon: str = ""
    tags: list[str]
    is_active: bool
    source_type: str = "manual"
    usage_count: int = 0
    last_used_at: Optional[datetime] = None
    created_by: Optional[uuid.UUID] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class SkillDetailResponse(BaseModel):
    """Detail response — includes content."""

    id: uuid.UUID
    name: str
    description: str
    content: str
    version: str
    license: str = ""
    compatibility: str = ""
    icon: str = ""
    metadata_extra: dict[str, Any] = Field(default_factory=dict)
    source_type: str = "manual"
    source_path: Optional[str] = None
    tags: list[str]
    is_active: bool
    usage_count: int = 0
    last_used_at: Optional[datetime] = None
    created_by: Optional[uuid.UUID] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Skill Square responses (Phase H3) ───────────────────────────────────────


class SquareCardItem(BaseModel):
    """Single card in the skill square list — aggregated by skill name."""
    name: str
    description: str
    icon: str = ""
    tags: list[str] = Field(default_factory=list)
    latest_version: str
    available_versions: list[str] = Field(default_factory=list)
    usage_count_total: int = 0
    installed: bool = False
    installed_version: Optional[str] = None
    enabled: Optional[bool] = None


class SquareTreeNode(BaseModel):
    """File/directory node for package tree preview."""
    name: str
    path: str
    type: str  # "file" | "dir"
    children: Optional[list["SquareTreeNode"]] = None


class SquareVersionInfo(BaseModel):
    """Version entry in the detail versions list."""
    version: str
    skill_id: uuid.UUID
    created_at: datetime


class SquareDetailResponse(BaseModel):
    """Skill square detail — single skill, version-resolved."""
    name: str
    description: str
    icon: str = ""
    tags: list[str] = Field(default_factory=list)
    selected_version: str
    versions: list[SquareVersionInfo] = Field(default_factory=list)
    installed: bool = False
    installed_version: Optional[str] = None
    enabled: Optional[bool] = None
    selected_skill_id: uuid.UUID
    readme_content: str = ""
    tree: list[SquareTreeNode] = Field(default_factory=list)
    usage_count_total: int = 0
