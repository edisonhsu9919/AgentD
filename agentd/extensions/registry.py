"""Process-local registry for loaded AgentD extensions."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from extensions.manifest import ExtensionManifest
from tools.base import BaseTool


@dataclass
class LoadedRouter:
    extension_name: str
    router: APIRouter
    prefix: str


@dataclass
class LoadedTool:
    extension_name: str
    tool: BaseTool


@dataclass
class PromptFragment:
    extension_name: str
    content: str
    path: str


@dataclass
class AgentProfile:
    extension_name: str
    name: str
    display_name: str
    content: str
    path: str
    allowed_tools: list[str] = field(default_factory=list)
    default_permission_mode: str = "default"
    prompt_mode: str = "standard"


@dataclass
class ExtensionRuntime:
    manifest: ExtensionManifest | None
    root_dir: Path
    status: str
    error: str | None = None
    router: LoadedRouter | None = None
    tools: list[LoadedTool] = field(default_factory=list)
    prompt_fragment: PromptFragment | None = None
    agent_profiles: list[AgentProfile] = field(default_factory=list)

    @property
    def name(self) -> str:
        if self.manifest:
            return self.manifest.name
        return self.root_dir.name


class ExtensionRegistry:
    def __init__(self) -> None:
        self._extensions: dict[str, ExtensionRuntime] = {}
        self._loaded = False

    @property
    def loaded(self) -> bool:
        return self._loaded

    def mark_loaded(self) -> None:
        self._loaded = True

    def register_runtime(self, runtime: ExtensionRuntime) -> None:
        self._extensions[runtime.name] = runtime

    def all_runtimes(self) -> list[ExtensionRuntime]:
        return sorted(
            self._extensions.values(),
            key=lambda runtime: (
                runtime.manifest.nav.order if runtime.manifest and runtime.manifest.nav else 100,
                runtime.name,
            ),
        )

    def get_runtime(self, name: str) -> ExtensionRuntime | None:
        return self._extensions.get(name)

    def get_enabled_extensions(self, user_role: str | None = None) -> list[ExtensionRuntime]:
        role = user_role or "user"
        result: list[ExtensionRuntime] = []
        for runtime in self.all_runtimes():
            manifest = runtime.manifest
            if not manifest:
                continue
            if runtime.status == "enabled":
                if manifest.visibility == "admin" and role != "admin":
                    continue
                result.append(runtime)
            elif runtime.status == "error" and role == "admin":
                result.append(runtime)
        return result

    def get_routers(self) -> list[LoadedRouter]:
        return [
            runtime.router
            for runtime in self.all_runtimes()
            if runtime.status == "enabled" and runtime.router is not None
        ]

    def get_loaded_tools(self) -> list[LoadedTool]:
        tools: list[LoadedTool] = []
        for runtime in self.all_runtimes():
            if runtime.status == "enabled":
                tools.extend(runtime.tools)
        return tools

    def get_tools(self) -> list[BaseTool]:
        return [loaded.tool for loaded in self.get_loaded_tools()]

    def get_prompt_fragments(self) -> list[PromptFragment]:
        return [
            runtime.prompt_fragment
            for runtime in self.all_runtimes()
            if runtime.status == "enabled" and runtime.prompt_fragment is not None
        ]

    def get_agent_profiles(self) -> list[AgentProfile]:
        profiles: list[AgentProfile] = []
        for runtime in self.all_runtimes():
            if runtime.status == "enabled":
                profiles.extend(runtime.agent_profiles)
        return profiles

    def get_agent_profile(self, name: str) -> AgentProfile | None:
        for profile in self.get_agent_profiles():
            if profile.name == name:
                return profile
        return None

    def mark_error(self, name: str, error: str) -> None:
        runtime = self._extensions.get(name)
        if runtime:
            runtime.status = "error"
            runtime.error = error

    def metadata_for_role(self, user_role: str | None) -> list[dict[str, Any]]:
        include_error = user_role == "admin"
        return [
            runtime.manifest.public_metadata(
                include_error=include_error,
                status=runtime.status,
                error=runtime.error,
            )
            for runtime in self.get_enabled_extensions(user_role)
            if runtime.manifest is not None
        ]


_registry = ExtensionRegistry()


def raw_extension_registry() -> ExtensionRegistry:
    return _registry


def get_extension_registry() -> ExtensionRegistry:
    from extensions.loader import ensure_extensions_loaded

    ensure_extensions_loaded()
    return _registry


def get_loaded_extension_tools() -> list[LoadedTool]:
    return get_extension_registry().get_loaded_tools()


def get_extension_tools() -> list[BaseTool]:
    return get_extension_registry().get_tools()


def get_extension_prompt_fragments() -> list[PromptFragment]:
    return get_extension_registry().get_prompt_fragments()


def get_extension_agent_profiles() -> list[AgentProfile]:
    return get_extension_registry().get_agent_profiles()


def get_extension_agent_profile(name: str) -> AgentProfile | None:
    return get_extension_registry().get_agent_profile(name)


def reset_extension_registry_for_tests() -> None:
    global _registry
    _registry = ExtensionRegistry()
