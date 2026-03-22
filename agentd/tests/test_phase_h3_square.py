"""Tests for Phase H3 — Skill Square backend.

Covers:
  - Square schemas
  - Catalog aggregation (package tree builder)
  - Square router endpoint existence
  - Package tree preview logic
"""

import os
import uuid
from datetime import datetime, timezone

import pytest

from workspace.manager import ensure_user_root


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def catalog_root(tmp_path, monkeypatch):
    """Create a temporary catalog root for testing."""
    from core import config
    monkeypatch.setattr(config.settings, "workspace_root", str(tmp_path))
    catalog = os.path.join(str(tmp_path), "_catalog", "skills")
    os.makedirs(catalog, exist_ok=True)
    return catalog


@pytest.fixture
def user_root(tmp_path):
    root = os.path.join(str(tmp_path), "test-user")
    ensure_user_root(root)
    return root


def _write_catalog_skill(catalog_root, name, version, desc="A skill", tags=None, body="Content here"):
    """Helper to write a catalog skill for testing."""
    version_dir = os.path.join(catalog_root, name, version)
    os.makedirs(version_dir, exist_ok=True)
    tags_str = "[" + ", ".join(tags or []) + "]"
    md = f"---\nname: {name}\ndescription: {desc}\nversion: {version}\ntags: {tags_str}\n---\n\n{body}\n"
    with open(os.path.join(version_dir, "SKILL.md"), "w") as f:
        f.write(md)
    return version_dir


# ═══════════════════════════════════════════════════════════════════════════════
# H3: Square schemas
# ═══════════════════════════════════════════════════════════════════════════════


class TestSquareSchemas:
    def test_square_card_item(self):
        from skills.schemas import SquareCardItem
        card = SquareCardItem(
            name="code_review",
            description="Review code",
            icon="🔍",
            tags=["python"],
            latest_version="1.0.0",
            available_versions=["1.0.0", "0.9.0"],
            usage_count_total=42,
            installed=True,
            installed_version="1.0.0",
            enabled=True,
        )
        assert card.name == "code_review"
        assert card.usage_count_total == 42
        assert len(card.available_versions) == 2
        assert card.installed is True

    def test_square_card_defaults(self):
        from skills.schemas import SquareCardItem
        card = SquareCardItem(
            name="test",
            description="desc",
            latest_version="0.1.0",
        )
        assert card.installed is False
        assert card.installed_version is None
        assert card.enabled is None
        assert card.icon == ""
        assert card.usage_count_total == 0

    def test_square_detail_response(self):
        from skills.schemas import SquareDetailResponse, SquareVersionInfo
        detail = SquareDetailResponse(
            name="test_skill",
            description="A test",
            selected_version="1.0.0",
            versions=[
                SquareVersionInfo(
                    version="1.0.0",
                    skill_id=uuid.uuid4(),
                    created_at=datetime.now(timezone.utc),
                ),
            ],
            selected_skill_id=uuid.uuid4(),
            readme_content="# Hello",
            usage_count_total=10,
        )
        assert detail.name == "test_skill"
        assert detail.selected_version == "1.0.0"
        assert len(detail.versions) == 1
        assert detail.readme_content == "# Hello"

    def test_square_tree_node(self):
        from skills.schemas import SquareTreeNode
        node = SquareTreeNode(
            name="references",
            path="references",
            type="dir",
            children=[
                SquareTreeNode(name="api.md", path="references/api.md", type="file"),
            ],
        )
        assert node.type == "dir"
        assert len(node.children) == 1
        assert node.children[0].name == "api.md"

    def test_square_version_info(self):
        from skills.schemas import SquareVersionInfo
        vi = SquareVersionInfo(
            version="2.0.0",
            skill_id=uuid.uuid4(),
            created_at=datetime.now(timezone.utc),
        )
        assert vi.version == "2.0.0"


# ═══════════════════════════════════════════════════════════════════════════════
# H3: Package tree builder
# ═══════════════════════════════════════════════════════════════════════════════


class TestPackageTreeBuilder:
    def test_tree_includes_skill_md(self, catalog_root):
        _write_catalog_skill(catalog_root, "my_skill", "1.0.0")
        from skills.square_service import _build_package_tree
        tree = _build_package_tree("my_skill", "1.0.0")
        names = [n["name"] for n in tree]
        assert "SKILL.md" in names

    def test_tree_includes_allowed_dirs(self, catalog_root):
        vdir = _write_catalog_skill(catalog_root, "rich_skill", "1.0.0")
        # Create allowed subdirs
        os.makedirs(os.path.join(vdir, "references"))
        with open(os.path.join(vdir, "references", "api.md"), "w") as f:
            f.write("# API")
        os.makedirs(os.path.join(vdir, "assets"))
        with open(os.path.join(vdir, "assets", "logo.png"), "w") as f:
            f.write("fake png")

        from skills.square_service import _build_package_tree
        tree = _build_package_tree("rich_skill", "1.0.0")
        names = [n["name"] for n in tree]
        assert "SKILL.md" in names
        assert "references" in names
        assert "assets" in names

        # Check children
        refs = next(n for n in tree if n["name"] == "references")
        assert refs["type"] == "dir"
        assert any(c["name"] == "api.md" for c in refs["children"])

    def test_tree_excludes_unlisted_dirs(self, catalog_root):
        vdir = _write_catalog_skill(catalog_root, "sneaky", "1.0.0")
        os.makedirs(os.path.join(vdir, "secret"))
        with open(os.path.join(vdir, "secret", "key.pem"), "w") as f:
            f.write("secret")

        from skills.square_service import _build_package_tree
        tree = _build_package_tree("sneaky", "1.0.0")
        names = [n["name"] for n in tree]
        assert "secret" not in names

    def test_tree_excludes_dotfiles(self, catalog_root):
        vdir = _write_catalog_skill(catalog_root, "dotty", "1.0.0")
        os.makedirs(os.path.join(vdir, "references"))
        with open(os.path.join(vdir, "references", ".hidden"), "w") as f:
            f.write("hidden")
        with open(os.path.join(vdir, "references", "visible.md"), "w") as f:
            f.write("visible")

        from skills.square_service import _build_package_tree
        tree = _build_package_tree("dotty", "1.0.0")
        refs = next(n for n in tree if n["name"] == "references")
        child_names = [c["name"] for c in refs["children"]]
        assert ".hidden" not in child_names
        assert "visible.md" in child_names

    def test_tree_nonexistent_returns_empty(self, catalog_root):
        from skills.square_service import _build_package_tree
        tree = _build_package_tree("nonexistent", "9.9.9")
        assert tree == []


# ═══════════════════════════════════════════════════════════════════════════════
# H3: Router endpoint registration
# ═══════════════════════════════════════════════════════════════════════════════


class TestSquareRouterEndpoints:
    def test_square_list_endpoint_exists(self):
        from skills.router import router
        paths = [route.path for route in router.routes]
        assert "/square" in paths

    def test_square_detail_endpoint_exists(self):
        from skills.router import router
        paths = [route.path for route in router.routes]
        assert "/square/{skill_name}" in paths

    def test_square_before_skill_id(self):
        """Square endpoints must be registered before /{skill_id} to avoid catch."""
        from skills.router import router
        paths = [route.path for route in router.routes]
        square_idx = paths.index("/square")
        skill_id_idx = paths.index("/{skill_id}")
        assert square_idx < skill_id_idx


# ═══════════════════════════════════════════════════════════════════════════════
# H3: Square service importable
# ═══════════════════════════════════════════════════════════════════════════════


class TestSquareServiceImport:
    def test_service_importable(self):
        from skills import square_service as sq_svc
        assert hasattr(sq_svc, "list_square_cards")
        assert hasattr(sq_svc, "get_square_detail")

    def test_tree_builder_importable(self):
        from skills.square_service import _build_package_tree
        assert callable(_build_package_tree)


# ═══════════════════════════════════════════════════════════════════════════════
# H3: Readme content extraction
# ═══════════════════════════════════════════════════════════════════════════════


class TestReadmeContentExtraction:
    def test_strip_frontmatter_for_readme(self, catalog_root):
        _write_catalog_skill(
            catalog_root, "readme_test", "1.0.0",
            body="# Main Content\n\nThis is the body.",
        )
        from skills.filesystem import read_catalog_skill_md
        from skills.package import strip_frontmatter

        raw = read_catalog_skill_md("readme_test", "1.0.0")
        assert raw is not None
        body = strip_frontmatter(raw)
        assert "# Main Content" in body
        assert "---" not in body
        assert "name:" not in body

    def test_readme_not_found(self, catalog_root):
        from skills.filesystem import read_catalog_skill_md
        result = read_catalog_skill_md("no_such_skill", "0.0.0")
        assert result is None
