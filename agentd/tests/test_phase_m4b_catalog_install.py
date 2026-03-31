"""Phase M4-B — Catalog/Install chain with venv auto-build tests.

Tests cover:
- validate_package detects requirements.txt
- import_package_to_catalog copies requirements.txt
- import_package_to_catalog auto-builds .venv (mocked subprocess)
- import_package_to_catalog fail-fast cleanup on venv build failure
- import_package_to_catalog fail-fast cleanup on pip install failure
- import_package_to_catalog without requirements.txt (no venv built)
- install_skill_for_user excludes .venv
- install_skill_for_user copies requirements.txt + scripts but not .venv
- SkillImportError is importable
"""

import os
import shutil
import sys
from unittest.mock import patch, MagicMock

import pytest

from skills.package import (
    SkillPackageMeta,
    validate_package,
)
from skills.filesystem import (
    SkillImportError,
    import_package_to_catalog,
    install_skill_for_user,
    get_catalog_dir,
    _build_skill_venv,
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


def _create_skill_package(
    base_dir,
    name,
    desc="A test skill",
    version="1.0.0",
    with_scripts=False,
    with_requirements=False,
    requirements_content="pdfplumber>=0.10\n",
):
    """Helper: create a skill package directory."""
    pkg_dir = os.path.join(base_dir, name)
    os.makedirs(pkg_dir, exist_ok=True)
    content = f"""---
name: {name}
description: {desc}
version: {version}
tags: [test]
---

Skill body.
"""
    with open(os.path.join(pkg_dir, "SKILL.md"), "w") as f:
        f.write(content)
    if with_scripts:
        scripts_dir = os.path.join(pkg_dir, "scripts")
        os.makedirs(scripts_dir, exist_ok=True)
        with open(os.path.join(scripts_dir, "helper.py"), "w") as f:
            f.write("# helper script\nprint('hello')\n")
    if with_requirements:
        with open(os.path.join(pkg_dir, "requirements.txt"), "w") as f:
            f.write(requirements_content)
    return pkg_dir


def _mock_subprocess_success(*args, **kwargs):
    """Mock subprocess.run that always succeeds."""
    return MagicMock(returncode=0)


# ── Test: validate_package detects requirements.txt ──────────────────────────


class TestValidatePackageRequirements:

    def test_has_requirements_true(self, tmp_path):
        pkg = _create_skill_package(
            str(tmp_path), "with-reqs", with_requirements=True,
        )
        result = validate_package(pkg)
        assert result.valid is True
        assert result.meta.has_requirements is True

    def test_has_requirements_false(self, tmp_path):
        pkg = _create_skill_package(
            str(tmp_path), "no-reqs", with_requirements=False,
        )
        result = validate_package(pkg)
        assert result.valid is True
        assert result.meta.has_requirements is False

    def test_has_requirements_with_scripts(self, tmp_path):
        pkg = _create_skill_package(
            str(tmp_path), "both",
            with_scripts=True, with_requirements=True,
        )
        result = validate_package(pkg)
        assert result.valid is True
        assert result.meta.has_scripts is True
        assert result.meta.has_requirements is True


# ── Test: import_package_to_catalog copies requirements.txt ──────────────────


class TestImportCopiesRequirements:

    @patch("skills.filesystem._build_skill_venv")
    def test_copies_requirements_txt(self, mock_build, tmp_workspace):
        src = os.path.join(tmp_workspace, "_src")
        pkg = _create_skill_package(
            src, "pdf-rename", version="1.1.0",
            with_requirements=True,
        )
        meta = validate_package(pkg).meta
        version_dir = import_package_to_catalog(pkg, meta)

        reqs_path = os.path.join(version_dir, "requirements.txt")
        assert os.path.isfile(reqs_path)
        with open(reqs_path) as f:
            assert "pdfplumber" in f.read()
        mock_build.assert_called_once_with(version_dir)

    @patch("skills.filesystem._build_skill_venv")
    def test_no_requirements_no_venv_build(self, mock_build, tmp_workspace):
        src = os.path.join(tmp_workspace, "_src")
        pkg = _create_skill_package(
            src, "simple", version="1.0.0",
            with_requirements=False,
        )
        meta = validate_package(pkg).meta
        version_dir = import_package_to_catalog(pkg, meta)

        assert not os.path.isfile(os.path.join(version_dir, "requirements.txt"))
        mock_build.assert_not_called()

    @patch("skills.filesystem._build_skill_venv")
    def test_copies_scripts_and_requirements(self, mock_build, tmp_workspace):
        src = os.path.join(tmp_workspace, "_src")
        pkg = _create_skill_package(
            src, "full-pkg", version="1.0.0",
            with_scripts=True, with_requirements=True,
        )
        meta = validate_package(pkg).meta
        version_dir = import_package_to_catalog(pkg, meta)

        assert os.path.isfile(os.path.join(version_dir, "SKILL.md"))
        assert os.path.isfile(os.path.join(version_dir, "requirements.txt"))
        assert os.path.isdir(os.path.join(version_dir, "scripts"))
        assert os.path.isfile(os.path.join(version_dir, "scripts", "helper.py"))


# ── Test: import fail-fast cleanup ───────────────────────────────────────────


class TestImportFailFastCleanup:

    @patch("skills.filesystem._build_skill_venv")
    def test_cleanup_on_venv_build_failure(self, mock_build, tmp_workspace):
        mock_build.side_effect = SkillImportError("venv creation failed")

        src = os.path.join(tmp_workspace, "_src")
        pkg = _create_skill_package(
            src, "fail-skill", version="1.0.0",
            with_requirements=True,
        )
        meta = validate_package(pkg).meta

        with pytest.raises(SkillImportError, match="venv creation failed"):
            import_package_to_catalog(pkg, meta)

        # Version directory should be cleaned up
        catalog_dir = get_catalog_dir()
        version_dir = os.path.join(catalog_dir, "fail-skill", "1.0.0")
        assert not os.path.exists(version_dir)

    @patch("skills.filesystem._build_skill_venv")
    def test_cleanup_removes_empty_parent(self, mock_build, tmp_workspace):
        mock_build.side_effect = SkillImportError("pip failed")

        src = os.path.join(tmp_workspace, "_src")
        pkg = _create_skill_package(
            src, "orphan-skill", version="1.0.0",
            with_requirements=True,
        )
        meta = validate_package(pkg).meta

        with pytest.raises(SkillImportError):
            import_package_to_catalog(pkg, meta)

        catalog_dir = get_catalog_dir()
        # Parent skill dir should also be cleaned up if empty
        assert not os.path.exists(os.path.join(catalog_dir, "orphan-skill"))

    @patch("skills.filesystem._build_skill_venv")
    def test_cleanup_preserves_other_versions(self, mock_build, tmp_workspace):
        """If another version exists, parent dir is preserved."""
        src = os.path.join(tmp_workspace, "_src")

        # First import succeeds (no requirements)
        pkg1 = _create_skill_package(
            src, "multi-ver", version="1.0.0",
            with_requirements=False,
        )
        meta1 = validate_package(pkg1).meta
        import_package_to_catalog(pkg1, meta1)

        # Second import fails
        mock_build.side_effect = SkillImportError("pip failed")
        pkg2_dir = os.path.join(src, "multi-ver-v2")
        os.makedirs(pkg2_dir, exist_ok=True)
        with open(os.path.join(pkg2_dir, "SKILL.md"), "w") as f:
            f.write("---\nname: multi-ver\ndescription: test\nversion: 2.0.0\ntags: [test]\n---\nBody\n")
        with open(os.path.join(pkg2_dir, "requirements.txt"), "w") as f:
            f.write("badpkg\n")
        meta2 = validate_package(pkg2_dir).meta
        meta2.name = "multi-ver"

        with pytest.raises(SkillImportError):
            import_package_to_catalog(pkg2_dir, meta2)

        catalog_dir = get_catalog_dir()
        # v1.0.0 should still exist
        assert os.path.isdir(os.path.join(catalog_dir, "multi-ver", "1.0.0"))
        # v2.0.0 should be cleaned up
        assert not os.path.exists(os.path.join(catalog_dir, "multi-ver", "2.0.0"))

    @patch("skills.filesystem._build_skill_venv")
    def test_unexpected_error_wraps_in_skill_import_error(self, mock_build, tmp_workspace):
        mock_build.side_effect = RuntimeError("unexpected OS error")

        src = os.path.join(tmp_workspace, "_src")
        pkg = _create_skill_package(
            src, "err-skill", version="1.0.0",
            with_requirements=True,
        )
        meta = validate_package(pkg).meta

        with pytest.raises(SkillImportError, match="Import failed"):
            import_package_to_catalog(pkg, meta)


# ── Test: install_skill_for_user excludes .venv ──────────────────────────────


class TestInstallExcludesVenv:

    def test_excludes_venv_dir(self, tmp_workspace):
        user_root = os.path.join(tmp_workspace, "user1")
        ensure_user_root(user_root)

        # Manually create a catalog entry with a .venv
        catalog_dir = get_catalog_dir()
        version_dir = os.path.join(catalog_dir, "has-venv", "1.0.0")
        os.makedirs(version_dir)
        with open(os.path.join(version_dir, "SKILL.md"), "w") as f:
            f.write("---\nname: has-venv\ndescription: test\nversion: 1.0.0\ntags: []\n---\nBody\n")

        # Create a fake .venv
        venv_dir = os.path.join(version_dir, ".venv")
        os.makedirs(os.path.join(venv_dir, "bin"))
        with open(os.path.join(venv_dir, "bin", "python"), "w") as f:
            f.write("#!/usr/bin/env python\n")

        # Create scripts
        scripts_dir = os.path.join(version_dir, "scripts")
        os.makedirs(scripts_dir)
        with open(os.path.join(scripts_dir, "run.py"), "w") as f:
            f.write("print('run')\n")

        # Create requirements.txt
        with open(os.path.join(version_dir, "requirements.txt"), "w") as f:
            f.write("pdfplumber\n")

        # Install
        install_skill_for_user(user_root, "has-venv", "1.0.0")

        user_skill = os.path.join(user_root, "skills", "has-venv")
        # Should have these
        assert os.path.isfile(os.path.join(user_skill, "SKILL.md"))
        assert os.path.isdir(os.path.join(user_skill, "scripts"))
        assert os.path.isfile(os.path.join(user_skill, "scripts", "run.py"))
        assert os.path.isfile(os.path.join(user_skill, "requirements.txt"))
        # Should NOT have .venv
        assert not os.path.exists(os.path.join(user_skill, ".venv"))

    def test_install_without_venv_still_works(self, tmp_workspace):
        """Skills without .venv in catalog install normally."""
        user_root = os.path.join(tmp_workspace, "user2")
        ensure_user_root(user_root)

        catalog_dir = get_catalog_dir()
        version_dir = os.path.join(catalog_dir, "no-venv", "1.0.0")
        os.makedirs(version_dir)
        with open(os.path.join(version_dir, "SKILL.md"), "w") as f:
            f.write("---\nname: no-venv\ndescription: test\nversion: 1.0.0\ntags: []\n---\nBody\n")

        install_skill_for_user(user_root, "no-venv", "1.0.0")

        user_skill = os.path.join(user_root, "skills", "no-venv")
        assert os.path.isfile(os.path.join(user_skill, "SKILL.md"))


# ── Test: install rejects incomplete catalog (P1 — Codex audit) ──────────────


class TestInstallRejectsIncompleteCatalog:

    def test_raises_when_skill_md_missing(self, tmp_workspace):
        """Catalog dir exists but SKILL.md missing → FileNotFoundError."""
        user_root = os.path.join(tmp_workspace, "user-inc")
        ensure_user_root(user_root)

        catalog_dir = get_catalog_dir()
        version_dir = os.path.join(catalog_dir, "broken-skill", "1.0.0")
        os.makedirs(version_dir)
        # Only scripts dir, no SKILL.md
        scripts_dir = os.path.join(version_dir, "scripts")
        os.makedirs(scripts_dir)
        with open(os.path.join(scripts_dir, "run.py"), "w") as f:
            f.write("print('hello')\n")

        with pytest.raises(FileNotFoundError, match="missing SKILL.md"):
            install_skill_for_user(user_root, "broken-skill", "1.0.0")

    def test_succeeds_when_skill_md_present(self, tmp_workspace):
        """Catalog with SKILL.md → install succeeds."""
        user_root = os.path.join(tmp_workspace, "user-ok")
        ensure_user_root(user_root)

        catalog_dir = get_catalog_dir()
        version_dir = os.path.join(catalog_dir, "good-skill", "1.0.0")
        os.makedirs(version_dir)
        with open(os.path.join(version_dir, "SKILL.md"), "w") as f:
            f.write("---\nname: good-skill\ndescription: test\nversion: 1.0.0\n---\nBody\n")

        result = install_skill_for_user(user_root, "good-skill", "1.0.0")
        assert result is True

    def test_no_user_dir_residue_on_failure(self, tmp_workspace):
        """Incomplete catalog → user skill dir should NOT be created."""
        user_root = os.path.join(tmp_workspace, "user-clean")
        ensure_user_root(user_root)

        catalog_dir = get_catalog_dir()
        version_dir = os.path.join(catalog_dir, "no-md", "1.0.0")
        os.makedirs(version_dir)

        with pytest.raises(FileNotFoundError):
            install_skill_for_user(user_root, "no-md", "1.0.0")

        # User side should be untouched
        assert not os.path.exists(os.path.join(user_root, "skills", "no-md"))


# ── Test: _build_skill_venv unit tests ───────────────────────────────────────


class TestBuildSkillVenv:

    def test_skips_when_no_requirements(self, tmp_path):
        """No requirements.txt → no venv built."""
        version_dir = str(tmp_path / "skill" / "1.0.0")
        os.makedirs(version_dir)
        _build_skill_venv(version_dir)
        assert not os.path.exists(os.path.join(version_dir, ".venv"))

    @patch("skills.filesystem.subprocess.run")
    def test_calls_venv_and_pip(self, mock_run, tmp_path):
        """With requirements.txt, calls venv creation + pip install."""
        version_dir = str(tmp_path / "skill" / "1.0.0")
        os.makedirs(version_dir)
        with open(os.path.join(version_dir, "requirements.txt"), "w") as f:
            f.write("requests\n")

        # First call (venv) succeeds and creates the dir structure
        def side_effect(cmd, **kwargs):
            if "-m" in cmd and "venv" in cmd:
                # Simulate venv creation
                venv_dir = cmd[-1]
                os.makedirs(os.path.join(venv_dir, "bin"), exist_ok=True)
                with open(os.path.join(venv_dir, "bin", "pip"), "w") as f:
                    f.write("#!/usr/bin/env python\n")
                with open(os.path.join(venv_dir, "bin", "python"), "w") as f:
                    f.write("#!/usr/bin/env python\n")
            return MagicMock(returncode=0)

        mock_run.side_effect = side_effect
        _build_skill_venv(version_dir)

        assert mock_run.call_count == 2
        # First call: python -m venv ...
        first_call = mock_run.call_args_list[0]
        assert "-m" in first_call[0][0]
        assert "venv" in first_call[0][0]
        # Second call: pip install -r requirements.txt
        second_call = mock_run.call_args_list[1]
        assert "install" in second_call[0][0]
        assert "-r" in second_call[0][0]

    @patch("skills.filesystem.subprocess.run")
    def test_raises_on_venv_creation_failure(self, mock_run, tmp_path):
        """Venv creation failure → SkillImportError."""
        import subprocess
        version_dir = str(tmp_path / "skill" / "1.0.0")
        os.makedirs(version_dir)
        with open(os.path.join(version_dir, "requirements.txt"), "w") as f:
            f.write("requests\n")

        mock_run.side_effect = subprocess.CalledProcessError(1, "venv")

        with pytest.raises(SkillImportError, match="Failed to create .venv"):
            _build_skill_venv(version_dir)

    @patch("skills.filesystem.subprocess.run")
    def test_raises_on_pip_install_failure(self, mock_run, tmp_path):
        """Pip install failure → SkillImportError."""
        import subprocess as sp
        version_dir = str(tmp_path / "skill" / "1.0.0")
        os.makedirs(version_dir)
        with open(os.path.join(version_dir, "requirements.txt"), "w") as f:
            f.write("nonexistent-pkg-xyz\n")

        call_count = [0]

        def side_effect(cmd, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # venv creation succeeds
                venv_dir = cmd[-1]
                os.makedirs(os.path.join(venv_dir, "bin"), exist_ok=True)
                with open(os.path.join(venv_dir, "bin", "pip"), "w") as f:
                    f.write("#!/usr/bin/env python\n")
                with open(os.path.join(venv_dir, "bin", "python"), "w") as f:
                    f.write("#!/usr/bin/env python\n")
                return MagicMock(returncode=0)
            else:
                # pip install fails
                raise sp.CalledProcessError(1, "pip")

        mock_run.side_effect = side_effect

        with pytest.raises(SkillImportError, match="pip install failed"):
            _build_skill_venv(version_dir)

    @patch("skills.filesystem.subprocess.run")
    def test_raises_on_timeout(self, mock_run, tmp_path):
        """Timeout during venv creation → SkillImportError."""
        import subprocess as sp
        version_dir = str(tmp_path / "skill" / "1.0.0")
        os.makedirs(version_dir)
        with open(os.path.join(version_dir, "requirements.txt"), "w") as f:
            f.write("requests\n")

        mock_run.side_effect = sp.TimeoutExpired("venv", 120)

        with pytest.raises(SkillImportError, match="Failed to create .venv"):
            _build_skill_venv(version_dir)

    @patch("skills.filesystem.subprocess.run")
    def test_raises_when_pip_not_found(self, mock_run, tmp_path):
        """Venv created but pip binary missing → SkillImportError."""
        version_dir = str(tmp_path / "skill" / "1.0.0")
        os.makedirs(version_dir)
        with open(os.path.join(version_dir, "requirements.txt"), "w") as f:
            f.write("requests\n")

        def side_effect(cmd, **kwargs):
            # Create venv dir but WITHOUT pip
            venv_dir = cmd[-1]
            os.makedirs(os.path.join(venv_dir, "bin"), exist_ok=True)
            # Only python, no pip
            with open(os.path.join(venv_dir, "bin", "python"), "w") as f:
                f.write("#!/usr/bin/env python\n")
            return MagicMock(returncode=0)

        mock_run.side_effect = side_effect

        with pytest.raises(SkillImportError, match="pip not found"):
            _build_skill_venv(version_dir)


# ── Test: SkillImportError is accessible ─────────────────────────────────────


class TestSkillImportError:

    def test_importable(self):
        from skills.filesystem import SkillImportError
        assert issubclass(SkillImportError, Exception)

    def test_message(self):
        err = SkillImportError("test failure")
        assert str(err) == "test failure"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
