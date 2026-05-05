"""Extension discovery and loading."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import ValidationError

from core.config import settings
from extensions.manifest import ExtensionManifest
from extensions.registry import (
    AgentProfile,
    ExtensionRuntime,
    LoadedRouter,
    LoadedTool,
    PromptFragment,
    raw_extension_registry,
)
from tools.base import BaseTool


MANIFEST_FILENAME = "agentd_extension.json"
DEFAULT_EXTENSION_DIRS = [
    Path("/opt/agentd/extensions"),
    Path(__file__).resolve().parents[2] / "extensions",
]


def extension_search_dirs() -> list[Path]:
    raw = os.environ.get("AGENTD_EXTENSION_DIRS") or getattr(settings, "extension_dirs", "")
    if raw:
        return [Path(item).expanduser() for item in raw.split(os.pathsep) if item.strip()]
    return list(DEFAULT_EXTENSION_DIRS)


def ensure_extensions_loaded():
    registry = raw_extension_registry()
    if registry.loaded:
        return registry
    load_extensions()
    return registry


def load_extensions():
    registry = raw_extension_registry()
    if registry.loaded:
        return registry

    seen: set[str] = set()
    for base_dir in extension_search_dirs():
        if not base_dir.exists() or not base_dir.is_dir():
            continue
        for manifest_path in sorted(base_dir.glob(f"*/{MANIFEST_FILENAME}")):
            runtime = _load_one_extension(manifest_path)
            if runtime.manifest and runtime.manifest.name in seen:
                runtime.status = "error"
                runtime.error = f"duplicate extension name: {runtime.manifest.name}"
            if runtime.manifest:
                seen.add(runtime.manifest.name)
            registry.register_runtime(runtime)

    registry.mark_loaded()
    return registry


def _load_one_extension(manifest_path: Path) -> ExtensionRuntime:
    root_dir = manifest_path.parent
    manifest: ExtensionManifest | None = None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = ExtensionManifest.model_validate(data)
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        return ExtensionRuntime(
            manifest=None,
            root_dir=root_dir,
            status="error",
            error=f"manifest_error:{type(exc).__name__}: {exc}",
        )

    runtime = ExtensionRuntime(
        manifest=manifest,
        root_dir=root_dir,
        status="disabled" if not manifest.enabled else "enabled",
    )
    if not manifest.enabled:
        return runtime

    try:
        runtime.router = _load_router(root_dir, manifest)
        runtime.tools = _load_tools(root_dir, manifest)
        runtime.prompt_fragment = _load_prompt_fragment(root_dir, manifest)
        runtime.agent_profiles = _load_agent_profiles(root_dir, manifest)
    except Exception as exc:
        runtime.status = "error"
        runtime.error = f"load_error:{type(exc).__name__}: {exc}"
    return runtime


def _load_router(root_dir: Path, manifest: ExtensionManifest) -> LoadedRouter | None:
    backend = manifest.backend
    if not backend.router:
        return None
    if not backend.router_prefix:
        raise ValueError("backend.router_prefix is required when backend.router is set")
    router = _import_attr(root_dir, manifest.name, backend.router)
    if not isinstance(router, APIRouter):
        raise TypeError("backend.router must resolve to a FastAPI APIRouter")
    return LoadedRouter(
        extension_name=manifest.name,
        router=router,
        prefix=backend.router_prefix,
    )


def _load_tools(root_dir: Path, manifest: ExtensionManifest) -> list[LoadedTool]:
    backend = manifest.backend
    if not backend.tools:
        return []
    factory = _import_attr(root_dir, manifest.name, backend.tools)
    tools = factory() if callable(factory) else factory
    if tools is None:
        return []
    if isinstance(tools, BaseTool):
        tools = [tools]
    if not isinstance(tools, list):
        raise TypeError("backend.tools must resolve to BaseTool or list[BaseTool]")
    loaded: list[LoadedTool] = []
    for tool in tools:
        if not isinstance(tool, BaseTool):
            raise TypeError("extension tools must inherit BaseTool")
        loaded.append(LoadedTool(extension_name=manifest.name, tool=tool))
    return loaded


def _load_prompt_fragment(root_dir: Path, manifest: ExtensionManifest) -> PromptFragment | None:
    fragment = manifest.backend.prompt_fragment
    if not fragment:
        return None
    path = (root_dir / fragment).resolve()
    if not _is_relative_to(path, root_dir.resolve()):
        raise ValueError("backend.prompt_fragment must stay inside extension directory")
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        return None
    return PromptFragment(
        extension_name=manifest.name,
        content=content,
        path=str(path),
    )


def _load_agent_profiles(root_dir: Path, manifest: ExtensionManifest) -> list[AgentProfile]:
    profiles: list[AgentProfile] = []
    for profile in manifest.backend.agent_profiles:
        path = (root_dir / profile.prompt_path).resolve()
        if not _is_relative_to(path, root_dir.resolve()):
            raise ValueError("backend.agent_profiles[].prompt_path must stay inside extension directory")
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            raise ValueError(f"agent profile prompt is empty: {profile.name}")
        profiles.append(AgentProfile(
            extension_name=manifest.name,
            name=profile.name,
            display_name=profile.display_name or profile.name,
            content=content,
            path=str(path),
            allowed_tools=list(profile.allowed_tools),
            default_permission_mode=profile.default_permission_mode,
            prompt_mode=profile.prompt_mode,
        ))
    return profiles


def _import_attr(root_dir: Path, extension_name: str, ref: str) -> Any:
    module_ref, sep, attr = ref.partition(":")
    if not sep or not module_ref or not attr:
        raise ValueError(f"invalid import reference: {ref}")
    module = _load_extension_module(root_dir, extension_name, module_ref)
    value: Any = module
    for part in attr.split("."):
        value = getattr(value, part)
    return value


def _load_extension_module(root_dir: Path, extension_name: str, module_ref: str):
    module_parts = module_ref.split(".")
    module_path = root_dir.joinpath(*module_parts).with_suffix(".py")
    package_dir = root_dir.joinpath(*module_parts)
    if not module_path.exists() and package_dir.joinpath("__init__.py").exists():
        module_path = package_dir / "__init__.py"
    if not module_path.exists():
        raise ImportError(f"module {module_ref!r} not found in {root_dir}")

    package_root = f"agentd_ext_{extension_name.replace('-', '_')}"
    _ensure_extension_package(package_root, root_dir)
    qualified_name = f"{package_root}.{module_ref}"
    if qualified_name in sys.modules:
        del sys.modules[qualified_name]

    parent = package_root
    current_path = root_dir
    for part in module_parts[:-1]:
        parent = f"{parent}.{part}"
        current_path = current_path / part
        _ensure_extension_package(parent, current_path)

    spec = importlib.util.spec_from_file_location(qualified_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load module {module_ref!r}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified_name] = module
    spec.loader.exec_module(module)
    return module


def _ensure_extension_package(name: str, path: Path) -> None:
    existing = sys.modules.get(name)
    if existing is not None:
        return
    package = types.ModuleType(name)
    package.__path__ = [str(path)]  # type: ignore[attr-defined]
    package.__package__ = name
    sys.modules[name] = package


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False
