"""Phase P6-B — Knowledge Retrieval Tools tests.

Tests cover:
- knowledge_catalog: listing with permission filter, tag filter
- knowledge_search: text search with permission enforcement
- knowledge_read: partial read with permission check
- Registry registration and child profile inclusion
"""

import json
import os
from unittest.mock import AsyncMock

import pytest

from tools.base import ToolContext
from tools.knowledge_routing import reset_knowledge_route_state
from tools.registry import get_registry
from knowledge.store import (
    build_frontmatter,
    ensure_knowledge_dirs,
    write_knowledge_doc,
    save_raw_file,
)


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def knowledge_root(tmp_path, monkeypatch):
    monkeypatch.setattr("knowledge.store.settings.workspace_root", str(tmp_path))
    ensure_knowledge_dirs()
    return str(tmp_path / "knowledge")


@pytest.fixture
def populated_knowledge(knowledge_root):
    """Create a set of test knowledge documents."""
    # Public doc about insurance
    fm1 = build_frontmatter(
        title="Insurance Research",
        description="Research on director liability insurance",
        kind="pdf", owner="user-a", permission="public",
        tags=["insurance", "law"],
        author="Alice",
        source_file="insurance.pdf",
    )
    write_knowledge_doc("ins001", fm1,
        "# Insurance Research\n\nThis paper discusses director liability.\n\n"
        "Key findings:\n- Insurance adoption is increasing\n- Legal framework needs improvement\n"
    )
    save_raw_file("insurance.pdf", b"fake pdf")

    # Private doc owned by user-a
    fm2 = build_frontmatter(
        title="Internal Report",
        description="Confidential internal analysis",
        kind="docx", owner="user-a", permission="private",
        tags=["internal", "analysis"],
    )
    write_knowledge_doc("int001", fm2,
        "# Internal Report\n\nConfidential findings about the project.\n"
        "Revenue increased by 15%.\n"
    )

    # Public doc about technology
    fm3 = build_frontmatter(
        title="AI Trends 2026",
        description="Overview of AI industry trends",
        kind="pdf", owner="user-b", permission="public",
        tags=["AI", "technology"],
    )
    write_knowledge_doc("ai001", fm3,
        "# AI Trends\n\nLarge language models continue to advance.\n"
        "Enterprise adoption is accelerating.\n"
    )


def _make_ctx(user_id: str = "user-a", run_id: str = "") -> ToolContext:
    return ToolContext(
        user_id=user_id,
        session_id="test-session",
        user_root="/tmp/test",
        session_dir="/tmp/test/sessions/test",
        workspace_dir="/tmp/test/sessions/test",
        venv_bin="",
        publish=AsyncMock(),
        run_id=run_id,
    )


def _get_lc_tool(name: str, ctx: ToolContext):
    registry = get_registry()
    tools = registry.get_langchain_tools(ctx)
    for tool in tools:
        if tool.name == name:
            return tool
    raise AssertionError(f"Missing tool {name}")


# ── knowledge_catalog ────────────────────────────────────────────────────


class TestKnowledgeCatalog:
    @pytest.mark.asyncio
    async def test_lists_visible_docs(self, populated_knowledge):
        from tools.knowledge_catalog import KnowledgeCatalogTool
        tool = KnowledgeCatalogTool()
        result = await tool.execute(_make_ctx("user-a"))
        data = json.loads(result["output"])

        assert data["count"] == 3  # 2 public + 1 own private
        titles = {d["title"] for d in data["documents"]}
        assert "Insurance Research" in titles
        assert "Internal Report" in titles
        assert "AI Trends 2026" in titles

    @pytest.mark.asyncio
    async def test_hides_others_private(self, populated_knowledge):
        from tools.knowledge_catalog import KnowledgeCatalogTool
        tool = KnowledgeCatalogTool()
        result = await tool.execute(_make_ctx("user-b"))
        data = json.loads(result["output"])

        titles = {d["title"] for d in data["documents"]}
        assert "Internal Report" not in titles  # user-a's private
        assert "Insurance Research" in titles    # public

    @pytest.mark.asyncio
    async def test_tag_filter(self, populated_knowledge):
        from tools.knowledge_catalog import KnowledgeCatalogTool
        tool = KnowledgeCatalogTool()
        result = await tool.execute(_make_ctx("user-a"), tag_filter="insurance")
        data = json.loads(result["output"])

        assert data["count"] == 1
        assert data["documents"][0]["title"] == "Insurance Research"

    @pytest.mark.asyncio
    async def test_empty_knowledge(self, knowledge_root):
        from tools.knowledge_catalog import KnowledgeCatalogTool
        tool = KnowledgeCatalogTool()
        result = await tool.execute(_make_ctx("user-a"))
        data = json.loads(result["output"])
        assert data["count"] == 0


# ── knowledge_search ─────────────────────────────────────────────────────


class TestKnowledgeSearch:
    @pytest.mark.asyncio
    async def test_finds_matching_content(self, populated_knowledge):
        from tools.knowledge_search import KnowledgeSearchTool
        tool = KnowledgeSearchTool()
        result = await tool.execute(_make_ctx("user-a"), query="insurance")
        data = json.loads(result["output"])

        assert data["total_matches"] >= 1
        assert any("Insurance" in r["title"] for r in data["results"])

    @pytest.mark.asyncio
    async def test_respects_permission(self, populated_knowledge):
        from tools.knowledge_search import KnowledgeSearchTool
        tool = KnowledgeSearchTool()
        # Search for "Confidential" — only in user-a's private doc
        result = await tool.execute(_make_ctx("user-b"), query="Confidential")
        data = json.loads(result["output"])

        # user-b should NOT find it
        assert data["total_matches"] == 0

    @pytest.mark.asyncio
    async def test_regex_search(self, populated_knowledge):
        from tools.knowledge_search import KnowledgeSearchTool
        tool = KnowledgeSearchTool()
        result = await tool.execute(_make_ctx("user-a"), query="increased?.*15%")
        data = json.loads(result["output"])
        assert data["total_matches"] >= 1

    @pytest.mark.asyncio
    async def test_empty_query(self, populated_knowledge):
        from tools.knowledge_search import KnowledgeSearchTool
        tool = KnowledgeSearchTool()
        result = await tool.execute(_make_ctx("user-a"), query="")
        assert result["is_error"] is True


# ── knowledge_read ───────────────────────────────────────────────────────


class TestKnowledgeRead:
    @pytest.mark.asyncio
    async def test_reads_authorized_doc(self, populated_knowledge):
        from tools.knowledge_read import KnowledgeReadTool
        tool = KnowledgeReadTool()
        result = await tool.execute(_make_ctx("user-a"), doc_id="ins001")
        data = json.loads(result["output"])

        assert data["doc_id"] == "ins001"
        assert data["title"] == "Insurance Research"
        assert "director liability" in data["content"]
        assert data["total_lines"] > 0

    @pytest.mark.asyncio
    async def test_denies_unauthorized_doc(self, populated_knowledge):
        from tools.knowledge_read import KnowledgeReadTool
        tool = KnowledgeReadTool()
        # user-b trying to read user-a's private doc
        result = await tool.execute(_make_ctx("user-b"), doc_id="int001")
        assert result["is_error"] is True
        data = json.loads(result["output"])
        assert "denied" in data["error"].lower() or "not found" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_partial_read(self, populated_knowledge):
        from tools.knowledge_read import KnowledgeReadTool
        tool = KnowledgeReadTool()
        result = await tool.execute(_make_ctx("user-a"), doc_id="ins001", offset=1, limit=2)
        data = json.loads(result["output"])

        assert data["lines_returned"] == 2
        assert data["offset"] == 1
        assert data["has_more"] is True

    @pytest.mark.asyncio
    async def test_nonexistent_doc(self, populated_knowledge):
        from tools.knowledge_read import KnowledgeReadTool
        tool = KnowledgeReadTool()
        result = await tool.execute(_make_ctx("user-a"), doc_id="missing")
        assert result["is_error"] is True


# ── Registry ─────────────────────────────────────────────────────────────


class TestRegistryP6B:
    def test_tools_registered(self):
        registry = get_registry()
        assert "knowledge_catalog" in registry.tools
        assert "knowledge_search" in registry.tools
        assert "knowledge_read" in registry.tools

    def test_tool_count_is_16(self):
        registry = get_registry()
        assert len(registry.tools) == 16

    def test_child_profile_includes_knowledge_tools(self):
        registry = get_registry()
        ctx = _make_ctx()
        child_tools = registry.get_langchain_tools(ctx, tool_profile="child")
        child_names = {t.name for t in child_tools}
        assert "knowledge_catalog" in child_names
        assert "knowledge_search" in child_names
        assert "knowledge_read" in child_names

    def test_metadata(self):
        registry = get_registry()
        for name in ("knowledge_catalog", "knowledge_search", "knowledge_read"):
            meta = registry.get(name).metadata
            assert meta.is_read_only is True
            assert meta.access_scope == "system_scoped"

    @pytest.mark.asyncio
    async def test_registry_blocks_search_before_catalog(self, populated_knowledge):
        run_id = "run-search-block"
        reset_knowledge_route_state(run_id)
        search = _get_lc_tool("knowledge_search", _make_ctx(run_id=run_id))

        result = await search.ainvoke({"query": "insurance"})

        assert "knowledge_catalog first" in result

    @pytest.mark.asyncio
    async def test_registry_blocks_read_before_catalog(self, populated_knowledge):
        run_id = "run-read-block"
        reset_knowledge_route_state(run_id)
        reader = _get_lc_tool("knowledge_read", _make_ctx(run_id=run_id))

        result = await reader.ainvoke({"doc_id": "ins001"})

        assert "knowledge_catalog first" in result

    @pytest.mark.asyncio
    async def test_registry_allows_search_after_catalog(self, populated_knowledge):
        run_id = "run-search-allow"
        reset_knowledge_route_state(run_id)
        ctx = _make_ctx(run_id=run_id)
        catalog = _get_lc_tool("knowledge_catalog", ctx)
        search = _get_lc_tool("knowledge_search", ctx)

        await catalog.ainvoke({})
        result = await search.ainvoke({"query": "insurance"})
        data = json.loads(result)

        assert data["total_matches"] >= 1

    @pytest.mark.asyncio
    async def test_registry_allows_read_after_catalog_without_search(self, populated_knowledge):
        run_id = "run-read-allow"
        reset_knowledge_route_state(run_id)
        ctx = _make_ctx(run_id=run_id)
        catalog = _get_lc_tool("knowledge_catalog", ctx)
        reader = _get_lc_tool("knowledge_read", ctx)

        await catalog.ainvoke({})
        result = await reader.ainvoke({"doc_id": "ins001"})
        data = json.loads(result)

        assert data["doc_id"] == "ins001"
