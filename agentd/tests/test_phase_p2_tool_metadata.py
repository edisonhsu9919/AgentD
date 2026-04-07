"""Phase P2 — Tool Metadata & Execution Semantics tests.

Tests cover:
- ToolMetadata dataclass
- All live tools have metadata
- Registry reads metadata (no external permission dict)
- Permission evaluator uses metadata.default_permission
- Diagnostics exposes metadata
"""

from dataclasses import asdict

import pytest

from tools.base import ToolMetadata
from tools.registry import get_registry


# ── ToolMetadata dataclass ───────────────────────────────────────────────


class TestToolMetadata:
    def test_frozen(self):
        meta = ToolMetadata(
            default_permission="allow",
            is_read_only=True,
            is_destructive=False,
            is_concurrency_safe=True,
            can_run_in_background=True,
            result_compressibility="high",
            access_scope="session_only",
            mutates_session_state=False,
        )
        with pytest.raises(AttributeError):
            meta.is_read_only = False  # type: ignore

    def test_asdict(self):
        meta = ToolMetadata(
            default_permission="ask",
            is_read_only=False,
            is_destructive=True,
            is_concurrency_safe=False,
            can_run_in_background=True,
            result_compressibility="high",
            access_scope="unrestricted",
            mutates_session_state=False,
        )
        d = asdict(meta)
        assert d["default_permission"] == "ask"
        assert d["is_destructive"] is True
        assert d["access_scope"] == "unrestricted"
        assert len(d) == 9


# ── All live tools have metadata ─────────────────────────────────────────


class TestAllToolsHaveMetadata:
    EXPECTED_TOOLS = {
        "bash", "file_read", "file_write", "file_edit", "file_inspect",
        "skill", "list_dir", "glob", "grep", "planning", "todo_update",
    }

    def test_all_registered(self):
        registry = get_registry()
        registered = set(registry.tools.keys())
        assert self.EXPECTED_TOOLS.issubset(registered)

    def test_all_have_metadata(self):
        registry = get_registry()
        for name, tool in registry.tools.items():
            meta = tool.metadata
            assert isinstance(meta, ToolMetadata), f"{name} metadata is not ToolMetadata"

    def test_metadata_field_count(self):
        registry = get_registry()
        for name, tool in registry.tools.items():
            d = asdict(tool.metadata)
            assert len(d) == 9, f"{name} metadata has {len(d)} fields, expected 8"


# ── Specific tool metadata values ────────────────────────────────────────


class TestSpecificToolMetadata:
    def test_bash(self):
        meta = get_registry().get("bash").metadata
        assert meta.default_permission == "ask"
        assert meta.is_read_only is False
        assert meta.is_destructive is True
        assert meta.is_concurrency_safe is False
        assert meta.can_run_in_background is True
        assert meta.result_compressibility == "high"
        assert meta.access_scope == "unrestricted"
        assert meta.mutates_session_state is False

    def test_file_read(self):
        meta = get_registry().get("file_read").metadata
        assert meta.default_permission == "allow"
        assert meta.is_read_only is True
        assert meta.is_concurrency_safe is True
        assert meta.access_scope == "session_only"

    def test_file_write(self):
        meta = get_registry().get("file_write").metadata
        assert meta.default_permission == "ask"
        assert meta.is_read_only is False
        assert meta.is_destructive is False
        assert meta.access_scope == "session_only"

    def test_file_edit(self):
        meta = get_registry().get("file_edit").metadata
        assert meta.default_permission == "ask"
        assert meta.is_read_only is False
        assert meta.is_destructive is False

    def test_file_inspect(self):
        meta = get_registry().get("file_inspect").metadata
        assert meta.is_read_only is True
        assert meta.is_concurrency_safe is True
        assert meta.result_compressibility == "medium"

    def test_skill(self):
        meta = get_registry().get("skill").metadata
        assert meta.default_permission == "allow"
        assert meta.is_read_only is False
        assert meta.mutates_session_state is True
        assert meta.access_scope == "none"

    def test_planning(self):
        meta = get_registry().get("planning").metadata
        assert meta.mutates_session_state is True
        assert meta.result_compressibility == "low"
        assert meta.is_concurrency_safe is False

    def test_todo_update(self):
        meta = get_registry().get("todo_update").metadata
        assert meta.mutates_session_state is True
        assert meta.result_compressibility == "low"

    def test_read_only_tools(self):
        """All read-only tools should share these properties."""
        registry = get_registry()
        read_only_names = ["file_read", "file_inspect", "list_dir", "glob", "grep"]
        for name in read_only_names:
            meta = registry.get(name).metadata
            assert meta.is_read_only is True, f"{name} should be read_only"
            assert meta.is_destructive is False, f"{name} should not be destructive"
            assert meta.is_concurrency_safe is True, f"{name} should be concurrency safe"
            assert meta.can_run_in_background is True, f"{name} should support background"
            assert meta.mutates_session_state is False, f"{name} should not mutate state"

    def test_session_state_tools(self):
        """All session-state-mutating tools should share these properties."""
        registry = get_registry()
        state_names = ["planning", "todo_update", "skill"]
        for name in state_names:
            meta = registry.get(name).metadata
            assert meta.mutates_session_state is True, f"{name} should mutate state"
            assert meta.result_compressibility == "low", f"{name} should have low compressibility"
            assert meta.can_run_in_background is False, f"{name} should not run in background"


# ── Registry metadata API ────────────────────────────────────────────────


class TestRegistryMetadataAPI:
    def test_default_permission_from_metadata(self):
        registry = get_registry()
        assert registry.default_permission("bash") == "ask"
        assert registry.default_permission("file_read") == "allow"
        assert registry.default_permission("file_write") == "ask"
        assert registry.default_permission("planning") == "allow"

    def test_default_permission_unknown_tool(self):
        registry = get_registry()
        assert registry.default_permission("nonexistent") == "ask"

    def test_get_tool_metadata(self):
        registry = get_registry()
        meta = registry.get_tool_metadata("bash")
        assert meta is not None
        assert meta.is_destructive is True

    def test_get_tool_metadata_none(self):
        registry = get_registry()
        assert registry.get_tool_metadata("nonexistent") is None

    def test_list_tool_metadata(self):
        registry = get_registry()
        all_meta = registry.list_tool_metadata()
        assert len(all_meta) >= 11
        assert "bash" in all_meta
        assert "file_read" in all_meta
        # Each entry is a dict with 8 keys
        for name, meta_dict in all_meta.items():
            assert len(meta_dict) == 9, f"{name} metadata dict has {len(meta_dict)} keys"
            assert "default_permission" in meta_dict
            assert "access_scope" in meta_dict

    def test_no_external_permissions_dict(self):
        """Registry should not have _DEFAULT_PERMISSIONS anymore."""
        registry = get_registry()
        assert not hasattr(registry, "_DEFAULT_PERMISSIONS")


# ── Permission evaluator uses metadata ───────────────────────────────────


class TestPermissionEvaluatorUsesMetadata:
    def test_evaluator_respects_metadata_defaults(self):
        from permission.evaluator import evaluate
        from permission.policy import SessionPolicy

        # Manual mode — falls through to tool default
        policy = SessionPolicy(mode="manual", rules=[])

        assert evaluate(policy, "bash", {}) == "ask"
        assert evaluate(policy, "file_read", {}) == "allow"
        assert evaluate(policy, "file_write", {}) == "ask"
        assert evaluate(policy, "planning", {}) == "allow"
        assert evaluate(policy, "glob", {}) == "allow"
