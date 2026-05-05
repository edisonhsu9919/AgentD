"""Extension manifest schema and validation."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


EXTENSION_NAME_RE = re.compile(r"^[a-z0-9_-]+$")
CORE_API_PREFIXES = {
    "/api/admin",
    "/api/auth",
    "/api/knowledge",
    "/api/permissions",
    "/api/runtime",
    "/api/sessions",
    "/api/skills",
}


class ExtensionNav(BaseModel):
    label: str
    href: str
    order: int = 100


class ExtensionAgentProfile(BaseModel):
    name: str
    display_name: str = ""
    prompt_path: str
    allowed_tools: list[str] = Field(default_factory=list)
    default_permission_mode: Literal["default", "fsd"] = "default"
    prompt_mode: Literal["standard", "minimal_task"] = "standard"

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not EXTENSION_NAME_RE.match(value):
            raise ValueError("agent profile name must match [a-z0-9_-]+")
        return value


class ExtensionBackend(BaseModel):
    router: str | None = None
    router_prefix: str | None = None
    tools: str | None = None
    prompt_fragment: str | None = None
    agent_profiles: list[ExtensionAgentProfile] = Field(default_factory=list)


class ExtensionFrontend(BaseModel):
    page_kind: str = "generic_extension"
    page_schema_endpoint: str | None = None


class ExtensionManifest(BaseModel):
    name: str
    display_name: str
    description: str = ""
    version: str = "0.1.0"
    enabled: bool = True
    visibility: Literal["all", "admin"] = "all"
    nav: ExtensionNav | None = None
    backend: ExtensionBackend = Field(default_factory=ExtensionBackend)
    frontend: ExtensionFrontend | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not EXTENSION_NAME_RE.match(value):
            raise ValueError("extension name must match [a-z0-9_-]+")
        return value

    @model_validator(mode="after")
    def validate_paths(self) -> "ExtensionManifest":
        expected_api_prefix = f"/api/extensions/{self.name}"
        router_prefix = self.backend.router_prefix
        if router_prefix:
            if not router_prefix.startswith(expected_api_prefix):
                raise ValueError(
                    f"backend.router_prefix must start with {expected_api_prefix}"
                )
            if any(router_prefix == prefix or router_prefix.startswith(prefix + "/")
                   for prefix in CORE_API_PREFIXES):
                raise ValueError("extension router_prefix may not overlap core API")

        if self.nav:
            expected_nav_prefix = f"/extensions/{self.name}"
            if not self.nav.href.startswith(expected_nav_prefix):
                raise ValueError(f"nav.href must start with {expected_nav_prefix}")

        if self.frontend and self.frontend.page_schema_endpoint:
            endpoint = self.frontend.page_schema_endpoint
            if not endpoint.startswith(expected_api_prefix):
                raise ValueError(
                    f"frontend.page_schema_endpoint must start with {expected_api_prefix}"
                )
        return self

    def public_metadata(self, *, include_error: bool = False, status: str = "enabled", error: str | None = None) -> dict:
        data = {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "version": self.version,
            "status": status,
            "visibility": self.visibility,
            "nav": self.nav.model_dump(mode="json") if self.nav else None,
            "frontend": self.frontend.model_dump(mode="json") if self.frontend else None,
        }
        if include_error and error:
            data["error"] = error
        return data
