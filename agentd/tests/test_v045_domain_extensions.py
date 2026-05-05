"""v0.4.5 Phase C1 domain extension substrate tests."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from extensions.loader import load_extensions
from extensions.manifest import ExtensionManifest
from extensions.registry import get_extension_registry, reset_extension_registry_for_tests


def _write_extension(
    root: Path,
    name: str = "demo-domain",
    *,
    enabled: bool = True,
    visibility: str = "all",
    tool_name: str = "demo_domain_ping",
    router_prefix: str | None = None,
) -> Path:
    ext_dir = root / name
    backend = ext_dir / "backend"
    backend.mkdir(parents=True)
    (backend / "__init__.py").write_text("", encoding="utf-8")
    prefix = router_prefix or f"/api/extensions/{name}"
    manifest = {
        "name": name,
        "display_name": "Demo Domain",
        "description": "demo",
        "version": "0.1.0",
        "enabled": enabled,
        "visibility": visibility,
        "nav": {
            "label": "Demo",
            "href": f"/extensions/{name}",
            "order": 50,
        },
        "backend": {
            "router": "backend.router:router",
            "router_prefix": prefix,
            "tools": "backend.tools:get_tools",
            "prompt_fragment": "backend/prompt.md",
        },
        "frontend": {
            "page_kind": "generic_extension",
            "page_schema_endpoint": f"{prefix}/page-schema",
        },
    }
    (ext_dir / "agentd_extension.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    (backend / "router.py").write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/page-schema')\n"
        "async def page_schema():\n"
        "    return {'kind': 'info_panel', 'title': 'Demo'}\n",
        encoding="utf-8",
    )
    (backend / "tools.py").write_text(
        "from typing import Any\n"
        "from tools.base import BaseTool, ToolContext, ToolMetadata\n"
        "class DemoTool(BaseTool):\n"
        f"    @property\n    def name(self): return {tool_name!r}\n"
        "    @property\n    def description(self): return 'demo extension ping'\n"
        "    @property\n"
        "    def metadata(self):\n"
        "        return ToolMetadata(default_permission='allow', is_read_only=True, "
        "is_destructive=False, is_concurrency_safe=True, can_run_in_background=False, "
        "result_compressibility='low', access_scope='none', mutates_session_state=False)\n"
        "    def schema(self) -> dict[str, Any]:\n"
        "        return {'type': 'object', 'properties': {}, 'required': []}\n"
        "    async def execute(self, ctx: ToolContext, **kwargs: Any):\n"
        "        return {'output': 'pong', 'is_error': False}\n"
        "def get_tools(): return [DemoTool()]\n",
        encoding="utf-8",
    )
    (backend / "prompt.md").write_text(
        "Use this extension only for explicit demo-domain ping requests.",
        encoding="utf-8",
    )
    return ext_dir


@pytest.fixture(autouse=True)
def reset_extensions(monkeypatch):
    reset_extension_registry_for_tests()
    import tools.registry as tool_registry

    monkeypatch.setattr(tool_registry, "_registry", None)
    yield
    reset_extension_registry_for_tests()
    monkeypatch.setattr(tool_registry, "_registry", None)


def test_manifest_schema_accepts_valid_extension():
    manifest = ExtensionManifest.model_validate({
        "name": "example-domain",
        "display_name": "Example",
        "nav": {"label": "Example", "href": "/extensions/example-domain"},
        "backend": {
            "router_prefix": "/api/extensions/example-domain",
        },
        "frontend": {
            "page_schema_endpoint": "/api/extensions/example-domain/page-schema",
        },
    })

    assert manifest.name == "example-domain"


def test_manifest_rejects_invalid_name():
    with pytest.raises(ValidationError):
        ExtensionManifest.model_validate({
            "name": "Bad Domain",
            "display_name": "Bad",
        })


def test_manifest_rejects_router_prefix_outside_extension_namespace():
    with pytest.raises(ValidationError):
        ExtensionManifest.model_validate({
            "name": "demo-domain",
            "display_name": "Demo",
            "backend": {"router_prefix": "/api/sessions/demo-domain"},
        })


def test_disabled_extension_is_loaded_but_not_registered(tmp_path, monkeypatch):
    _write_extension(tmp_path, enabled=False)
    monkeypatch.setenv("AGENTD_EXTENSION_DIRS", str(tmp_path))

    registry = load_extensions()

    runtime = registry.get_runtime("demo-domain")
    assert runtime is not None
    assert runtime.status == "disabled"
    assert registry.get_routers() == []
    assert registry.get_tools() == []


def test_enabled_extension_metadata_and_router_are_available(tmp_path, monkeypatch):
    _write_extension(tmp_path)
    monkeypatch.setenv("AGENTD_EXTENSION_DIRS", str(tmp_path))

    registry = load_extensions()
    metadata = registry.metadata_for_role("user")

    assert metadata[0]["name"] == "demo-domain"
    assert metadata[0]["nav"]["href"] == "/extensions/demo-domain"

    app = FastAPI()
    for loaded_router in registry.get_routers():
        app.include_router(loaded_router.router, prefix=loaded_router.prefix)
    client = TestClient(app)

    response = client.get("/api/extensions/demo-domain/page-schema")

    assert response.status_code == 200
    assert response.json()["kind"] == "info_panel"


def test_admin_visibility_is_hidden_from_regular_user(tmp_path, monkeypatch):
    _write_extension(tmp_path, visibility="admin")
    monkeypatch.setenv("AGENTD_EXTENSION_DIRS", str(tmp_path))

    registry = load_extensions()

    assert registry.metadata_for_role("user") == []
    assert registry.metadata_for_role("admin")[0]["name"] == "demo-domain"


def test_example_tool_enters_tool_registry(tmp_path, monkeypatch):
    _write_extension(tmp_path)
    monkeypatch.setenv("AGENTD_EXTENSION_DIRS", str(tmp_path))

    from tools.registry import get_registry

    registry = get_registry()

    assert registry.get("demo_domain_ping") is not None


def test_extension_tool_name_conflict_marks_extension_error(tmp_path, monkeypatch):
    _write_extension(tmp_path, tool_name="bash")
    monkeypatch.setenv("AGENTD_EXTENSION_DIRS", str(tmp_path))

    from tools.registry import get_registry

    get_registry()
    runtime = get_extension_registry().get_runtime("demo-domain")

    assert runtime is not None
    assert runtime.status == "error"
    assert "conflicts with core tool" in (runtime.error or "")


def test_extension_prompt_fragment_enters_prompt_diagnostics(tmp_path, monkeypatch):
    _write_extension(tmp_path)
    monkeypatch.setenv("AGENTD_EXTENSION_DIRS", str(tmp_path))

    from agent.runtime import build_system_prompt

    prompt, diagnostics = build_system_prompt(
        agent_id="assistant",
        session_dir=str(tmp_path / "session"),
        user_root=str(tmp_path),
        model_id="test-model",
        session_id=str(uuid.uuid4()),
        loaded_skills=[],
    )

    assert "Domain Extensions Metadata" in prompt
    assert diagnostics["extensions_injected"] is True
    assert diagnostics["extensions_enabled"] == ["demo-domain"]
    assert diagnostics["extensions_prompt_chars"] > 0


def test_extension_metadata_router_filters_visibility(tmp_path, monkeypatch):
    _write_extension(tmp_path, visibility="admin")
    monkeypatch.setenv("AGENTD_EXTENSION_DIRS", str(tmp_path))
    load_extensions()

    from extensions.router import router
    from api.deps import get_current_user

    app = FastAPI()
    app.include_router(router, prefix="/api/extensions")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(role="user")
    client = TestClient(app)

    user_response = client.get("/api/extensions")
    assert user_response.status_code == 200
    assert user_response.json()["data"]["extensions"] == []

    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(role="admin")
    admin_response = client.get("/api/extensions")

    assert admin_response.status_code == 200
    assert admin_response.json()["data"]["extensions"][0]["name"] == "demo-domain"


def test_repo_example_extension_loads_by_default(monkeypatch):
    monkeypatch.delenv("AGENTD_EXTENSION_DIRS", raising=False)

    registry = load_extensions()
    runtime = registry.get_runtime("example-domain")

    assert runtime is not None
    assert runtime.status == "enabled"
    assert any(tool.tool.name == "example_domain_ping" for tool in registry.get_loaded_tools())


def test_repo_example_page_schema_uses_agentd_envelope(monkeypatch):
    monkeypatch.delenv("AGENTD_EXTENSION_DIRS", raising=False)
    registry = load_extensions()

    app = FastAPI()
    for loaded_router in registry.get_routers():
        app.include_router(loaded_router.router, prefix=loaded_router.prefix)
    client = TestClient(app)

    response = client.get("/api/extensions/example-domain/page-schema")
    body = response.json()

    assert response.status_code == 200
    assert body["data"]["kind"] == "info_panel"
    assert body["data"]["actions"][0] == {
        "label": "返回对话工作台",
        "href": "/chat",
        "variant": "secondary",
    }
