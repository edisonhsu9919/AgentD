"""Tests for Phase F1 — Skills package management system.

Covers:
  - skills.package: parse_frontmatter (enhanced), validate_package
  - skills.filesystem: versioned catalog write/remove/import, install/uninstall
"""

import os
import shutil

import pytest

from skills.package import (
    SkillPackageMeta,
    SkillPackageValidationResult,
    parse_frontmatter,
    strip_frontmatter,
    validate_package,
)
from skills.filesystem import (
    get_catalog_dir,
    get_skills_dir,
    import_package_to_catalog,
    install_skill_for_user,
    list_catalog_versions,
    get_latest_version,
    read_catalog_skill_md,
    remove_skill_from_catalog,
    uninstall_skill_for_user,
    write_skill_to_catalog,
)
from workspace.manager import ensure_user_root


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_workspace(tmp_path, monkeypatch):
    """Set workspace root to a temp directory."""
    from core import config
    monkeypatch.setattr(config.settings, "workspace_root", str(tmp_path))
    return str(tmp_path)


@pytest.fixture
def user_root(tmp_workspace):
    root = os.path.join(tmp_workspace, "test-user")
    ensure_user_root(root)
    return root


def _create_skill_package(base_dir, name, desc="A test skill", version="1.0.0",
                          license_val="MIT", body="Do things", extra_fields="",
                          with_refs=False, with_assets=False):
    """Helper: create a skill package directory with SKILL.md."""
    pkg_dir = os.path.join(base_dir, name)
    os.makedirs(pkg_dir, exist_ok=True)
    tags_str = "[test]"
    content = f"""---
name: {name}
description: {desc}
version: {version}
license: {license_val}
tags: {tags_str}
{extra_fields}---

{body}
"""
    with open(os.path.join(pkg_dir, "SKILL.md"), "w") as f:
        f.write(content)
    if with_refs:
        refs = os.path.join(pkg_dir, "references")
        os.makedirs(refs, exist_ok=True)
        with open(os.path.join(refs, "guide.md"), "w") as f:
            f.write("# Reference Guide\n")
    if with_assets:
        assets = os.path.join(pkg_dir, "assets")
        os.makedirs(assets, exist_ok=True)
        with open(os.path.join(assets, "icon.txt"), "w") as f:
            f.write("icon placeholder")
    return pkg_dir


# ── Test: Enhanced frontmatter parser ────────────────────────────────────────


class TestEnhancedFrontmatter:
    def test_parses_all_fields(self):
        content = """---
name: my-skill
description: Does cool stuff
version: 2.1.0
license: MIT
compatibility: agentd>=0.3.0
tags: [python, ai]
metadata:
  author: test
  category: dev
---

Body text.
"""
        meta = parse_frontmatter(content)
        assert meta["name"] == "my-skill"
        assert meta["description"] == "Does cool stuff"
        assert meta["version"] == "2.1.0"
        assert meta["license"] == "MIT"
        assert meta["compatibility"] == "agentd>=0.3.0"
        assert meta["tags"] == ["python", "ai"]
        assert meta["metadata"] == {"author": "test", "category": "dev"}

    def test_version_default_absent(self):
        content = """---
name: simple
description: Simple skill
---

Body.
"""
        meta = parse_frontmatter(content)
        assert meta["name"] == "simple"
        assert "version" not in meta  # default handled by caller

    def test_tags_empty_brackets(self):
        content = """---
name: t
description: d
tags: []
---

X
"""
        meta = parse_frontmatter(content)
        assert meta["tags"] == []

    def test_backward_compat_with_old_format(self):
        """Old SKILL.md without version/license still parses correctly."""
        content = """---
name: old_skill
description: Old format
tags: [dev, test]
---

Old body.
"""
        meta = parse_frontmatter(content)
        assert meta["name"] == "old_skill"
        assert meta["description"] == "Old format"
        assert meta["tags"] == ["dev", "test"]

    def test_strip_frontmatter(self):
        content = """---
name: test
version: 1.0.0
---

Body here.
"""
        body = strip_frontmatter(content)
        assert body.strip() == "Body here."


# ── Test: Package validation ─────────────────────────────────────────────────


class TestValidatePackage:
    def test_valid_package(self, tmp_path):
        pkg = _create_skill_package(str(tmp_path), "valid-skill", version="1.0.0")
        result = validate_package(pkg)
        assert result.valid is True
        assert result.meta.name == "valid-skill"
        assert result.meta.version == "1.0.0"

    def test_missing_skill_md(self, tmp_path):
        empty_dir = os.path.join(str(tmp_path), "empty")
        os.makedirs(empty_dir)
        result = validate_package(empty_dir)
        assert result.valid is False
        assert "Missing SKILL.md" in result.errors

    def test_missing_name(self, tmp_path):
        pkg = os.path.join(str(tmp_path), "no-name")
        os.makedirs(pkg)
        with open(os.path.join(pkg, "SKILL.md"), "w") as f:
            f.write("---\ndescription: has desc\n---\nBody\n")
        result = validate_package(pkg)
        assert result.valid is False
        assert any("name" in e for e in result.errors)

    def test_missing_description(self, tmp_path):
        pkg = os.path.join(str(tmp_path), "no-desc")
        os.makedirs(pkg)
        with open(os.path.join(pkg, "SKILL.md"), "w") as f:
            f.write("---\nname: test\n---\nBody\n")
        result = validate_package(pkg)
        assert result.valid is False
        assert any("description" in e for e in result.errors)

    def test_not_a_directory(self, tmp_path):
        result = validate_package(os.path.join(str(tmp_path), "nonexistent"))
        assert result.valid is False

    def test_detects_resource_dirs(self, tmp_path):
        pkg = _create_skill_package(str(tmp_path), "with-res", with_refs=True, with_assets=True)
        result = validate_package(pkg)
        assert result.valid is True
        assert result.meta.has_references is True
        assert result.meta.has_assets is True
        assert result.meta.has_scripts is False

    def test_version_defaults_to_010(self, tmp_path):
        pkg = os.path.join(str(tmp_path), "no-ver")
        os.makedirs(pkg)
        with open(os.path.join(pkg, "SKILL.md"), "w") as f:
            f.write("---\nname: nover\ndescription: test\n---\nBody\n")
        result = validate_package(pkg)
        assert result.valid is True
        assert result.meta.version == "0.1.0"


# ── Test: Versioned catalog filesystem ───────────────────────────────────────


class TestVersionedCatalog:
    def test_write_creates_versioned_dir(self, tmp_workspace):
        meta = SkillPackageMeta(name="git-release", description="Release helper", version="1.0.0")
        path = write_skill_to_catalog(meta, "Release instructions here")
        assert os.path.isfile(path)
        assert "/git-release/1.0.0/SKILL.md" in path
        with open(path) as f:
            content = f.read()
        assert "name: git-release" in content
        assert "version: 1.0.0" in content

    def test_multiple_versions_coexist(self, tmp_workspace):
        for ver in ("1.0.0", "1.1.0", "2.0.0"):
            meta = SkillPackageMeta(name="multi", description="Multi", version=ver)
            write_skill_to_catalog(meta, f"Body v{ver}")

        versions = list_catalog_versions("multi")
        assert versions == ["1.0.0", "1.1.0", "2.0.0"]

    def test_get_latest_version(self, tmp_workspace):
        for ver in ("0.1.0", "1.0.0"):
            meta = SkillPackageMeta(name="latest", description="Latest", version=ver)
            write_skill_to_catalog(meta, "body")
        assert get_latest_version("latest") == "1.0.0"

    def test_get_latest_version_nonexistent(self, tmp_workspace):
        assert get_latest_version("nonexistent") is None

    def test_read_catalog_skill_md(self, tmp_workspace):
        meta = SkillPackageMeta(name="readable", description="Read", version="1.0.0")
        write_skill_to_catalog(meta, "Content here")
        content = read_catalog_skill_md("readable", "1.0.0")
        assert content is not None
        assert "Content here" in content

    def test_read_nonexistent_version(self, tmp_workspace):
        assert read_catalog_skill_md("nope", "9.9.9") is None

    def test_remove_specific_version(self, tmp_workspace):
        for ver in ("1.0.0", "2.0.0"):
            meta = SkillPackageMeta(name="removable", description="R", version=ver)
            write_skill_to_catalog(meta, "body")

        remove_skill_from_catalog("removable", "1.0.0")
        versions = list_catalog_versions("removable")
        assert versions == ["2.0.0"]

    def test_remove_all_versions(self, tmp_workspace):
        meta = SkillPackageMeta(name="gone", description="G", version="1.0.0")
        write_skill_to_catalog(meta, "body")
        remove_skill_from_catalog("gone")
        assert list_catalog_versions("gone") == []

    def test_remove_last_version_cleans_parent(self, tmp_workspace):
        meta = SkillPackageMeta(name="cleanup", description="C", version="1.0.0")
        write_skill_to_catalog(meta, "body")
        remove_skill_from_catalog("cleanup", "1.0.0")
        catalog_dir = get_catalog_dir()
        assert not os.path.exists(os.path.join(catalog_dir, "cleanup"))


# ── Test: Import package to catalog ──────────────────────────────────────────


class TestImportPackage:
    def test_import_copies_skill_md(self, tmp_workspace):
        src = os.path.join(tmp_workspace, "_src")
        os.makedirs(src, exist_ok=True)
        pkg = _create_skill_package(src, "imported", version="1.2.0")
        meta = validate_package(pkg).meta
        version_dir = import_package_to_catalog(pkg, meta)

        assert os.path.isfile(os.path.join(version_dir, "SKILL.md"))
        assert "imported" in version_dir and "1.2.0" in version_dir

    def test_import_copies_resource_dirs(self, tmp_workspace):
        src = os.path.join(tmp_workspace, "_src2")
        os.makedirs(src, exist_ok=True)
        pkg = _create_skill_package(
            src, "with-res", version="1.0.0",
            with_refs=True, with_assets=True,
        )
        meta = validate_package(pkg).meta
        version_dir = import_package_to_catalog(pkg, meta)

        assert os.path.isdir(os.path.join(version_dir, "references"))
        assert os.path.isdir(os.path.join(version_dir, "assets"))
        assert os.path.isfile(os.path.join(version_dir, "references", "guide.md"))

    def test_import_overwrites_existing_version(self, tmp_workspace):
        src = os.path.join(tmp_workspace, "_src3")
        os.makedirs(src, exist_ok=True)
        pkg = _create_skill_package(src, "overwrite", version="1.0.0", body="v1")
        meta = validate_package(pkg).meta
        import_package_to_catalog(pkg, meta)

        # Create new package with same name/version but different body
        pkg2 = _create_skill_package(src, "overwrite2", version="1.0.0", body="v2")
        meta2 = validate_package(pkg2).meta
        meta2.name = "overwrite"  # same name, same version
        import_package_to_catalog(pkg2, meta2)

        content = read_catalog_skill_md("overwrite", "1.0.0")
        assert "v2" in content


# ── Test: Versioned install / uninstall ──────────────────────────────────────


class TestVersionedInstall:
    def test_install_latest(self, tmp_workspace):
        user_root = os.path.join(tmp_workspace, "user1")
        ensure_user_root(user_root)

        for ver in ("1.0.0", "2.0.0"):
            meta = SkillPackageMeta(name="versioned", description="V", version=ver, tags=["test"])
            write_skill_to_catalog(meta, f"Body {ver}")

        install_skill_for_user(user_root, "versioned")
        skill_md = os.path.join(user_root, "skills", "versioned", "SKILL.md")
        assert os.path.isfile(skill_md)
        with open(skill_md) as f:
            content = f.read()
        assert "version: 2.0.0" in content

    def test_install_specific_version(self, tmp_workspace):
        user_root = os.path.join(tmp_workspace, "user2")
        ensure_user_root(user_root)

        for ver in ("1.0.0", "2.0.0"):
            meta = SkillPackageMeta(name="pinned", description="P", version=ver)
            write_skill_to_catalog(meta, f"Body {ver}")

        install_skill_for_user(user_root, "pinned", "1.0.0")
        skill_md = os.path.join(user_root, "skills", "pinned", "SKILL.md")
        with open(skill_md) as f:
            content = f.read()
        assert "version: 1.0.0" in content

    def test_install_not_found(self, tmp_workspace):
        user_root = os.path.join(tmp_workspace, "user3")
        ensure_user_root(user_root)
        with pytest.raises(FileNotFoundError):
            install_skill_for_user(user_root, "nonexistent")

    def test_uninstall(self, tmp_workspace):
        user_root = os.path.join(tmp_workspace, "user4")
        ensure_user_root(user_root)

        meta = SkillPackageMeta(name="removeme", description="R", version="1.0.0")
        write_skill_to_catalog(meta, "body")
        install_skill_for_user(user_root, "removeme")
        assert uninstall_skill_for_user(user_root, "removeme") is True
        assert not os.path.exists(os.path.join(user_root, "skills", "removeme"))

    def test_uninstall_not_installed(self, tmp_workspace):
        user_root = os.path.join(tmp_workspace, "user5")
        ensure_user_root(user_root)
        assert uninstall_skill_for_user(user_root, "not_here") is False


# ── Test: Legacy compat — workspace.manager re-exports ───────────────────────


class TestLegacyCompat:
    def test_workspace_manager_reexports(self):
        """workspace.manager still exports all old names for backward compat."""
        from workspace.manager import (
            get_catalog_dir,
            get_skills_dir,
            install_skill_for_user,
            remove_skill_from_catalog,
            uninstall_skill_for_user,
            write_skill_to_catalog,
        )
        # Just verifying the imports work
        assert callable(write_skill_to_catalog)
        assert callable(install_skill_for_user)

    def test_legacy_write_creates_versioned_dir(self, tmp_workspace):
        """Legacy write_skill_to_catalog wrapper creates version 0.1.0."""
        from workspace.manager import write_skill_to_catalog as legacy_write
        path = legacy_write("legacy-skill", "desc", "body content", ["tag1"])
        assert os.path.isfile(path)
        assert "/legacy-skill/0.1.0/SKILL.md" in path
