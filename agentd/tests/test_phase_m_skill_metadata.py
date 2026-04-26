"""Phase M — Skill Metadata Continuity & Natural Recall tests.

Tests cover:
- M1: Filesystem-based skill metadata scanning
- M1: Skills metadata layer format (tags, section title)
- M1: build_system_prompt integration
- M2: Role prompt skill guidance (natural recall)
- M2: Metadata layer footer semantics
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── M1: Filesystem Skill Metadata Scanning ──────────────────────────────


class TestFetchUserInstalledSkillMetadata(unittest.TestCase):
    """Verify _fetch_user_installed_skill_metadata reads from disk."""

    def test_function_exists(self):
        from agent.runtime import _fetch_user_installed_skill_metadata
        self.assertTrue(callable(_fetch_user_installed_skill_metadata))

    def test_returns_none_for_missing_dir(self):
        from agent.runtime import _fetch_user_installed_skill_metadata
        result = _fetch_user_installed_skill_metadata("/nonexistent/path")
        self.assertIsNone(result)

    def test_returns_none_for_empty_skills_dir(self):
        from agent.runtime import _fetch_user_installed_skill_metadata
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / "skills"
            skills_dir.mkdir()
            result = _fetch_user_installed_skill_metadata(tmpdir)
            self.assertIsNone(result)

    def test_reads_skill_frontmatter_from_disk(self):
        from agent.runtime import _fetch_user_installed_skill_metadata
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "skills" / "test-skill"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                '---\nname: test-skill\ndescription: "A test skill"\n'
                'version: "2.0.0"\ntags: [test, demo]\n---\n# Test\n',
                encoding="utf-8",
            )
            result = _fetch_user_installed_skill_metadata(tmpdir)
            self.assertIsNotNone(result)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["name"], "test-skill")
            self.assertEqual(result[0]["version"], "2.0.0")
            self.assertEqual(result[0]["description"], "A test skill")
            self.assertEqual(result[0]["tags"], ["test", "demo"])

    def test_scans_multiple_skills(self):
        from agent.runtime import _fetch_user_installed_skill_metadata
        with tempfile.TemporaryDirectory() as tmpdir:
            for name in ("alpha", "beta", "gamma"):
                d = Path(tmpdir) / "skills" / name
                d.mkdir(parents=True)
                (d / "SKILL.md").write_text(
                    f'---\nname: {name}\ndescription: "Skill {name}"\n'
                    f'version: "1.0.0"\n---\n# {name}\n',
                    encoding="utf-8",
                )
            result = _fetch_user_installed_skill_metadata(tmpdir)
            self.assertEqual(len(result), 3)
            names = [s["name"] for s in result]
            self.assertEqual(names, ["alpha", "beta", "gamma"])

    def test_skips_dirs_without_skill_md(self):
        from agent.runtime import _fetch_user_installed_skill_metadata
        with tempfile.TemporaryDirectory() as tmpdir:
            # One valid skill, one empty dir
            valid = Path(tmpdir) / "skills" / "valid-skill"
            valid.mkdir(parents=True)
            (valid / "SKILL.md").write_text(
                '---\nname: valid-skill\ndescription: "OK"\nversion: "1.0.0"\n---\n',
                encoding="utf-8",
            )
            empty = Path(tmpdir) / "skills" / "empty-dir"
            empty.mkdir(parents=True)
            result = _fetch_user_installed_skill_metadata(tmpdir)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["name"], "valid-skill")

    def test_tags_default_to_empty_list(self):
        from agent.runtime import _fetch_user_installed_skill_metadata
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir) / "skills" / "no-tags"
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(
                '---\nname: no-tags\ndescription: "No tags"\nversion: "1.0.0"\n---\n',
                encoding="utf-8",
            )
            result = _fetch_user_installed_skill_metadata(tmpdir)
            self.assertEqual(result[0]["tags"], [])

    def test_is_sync_not_async(self):
        """M1: function is synchronous (no DB needed)."""
        import inspect
        from agent.runtime import _fetch_user_installed_skill_metadata
        self.assertFalse(inspect.iscoroutinefunction(_fetch_user_installed_skill_metadata))


# ── M1: Skills Metadata Layer Format ────────────────────────────────────


class TestSkillsMetadataLayerFormat(unittest.TestCase):
    """Verify _load_skills_metadata_layer output format."""

    def _make_skills(self, *names):
        return [
            {"name": n, "version": "1.0.0", "description": f"Skill {n}", "tags": ["t1"]}
            for n in names
        ]

    def test_section_title_is_available_session_skills(self):
        from agent.runtime import _load_skills_metadata_layer
        result = _load_skills_metadata_layer(self._make_skills("a"))
        self.assertIn("## Available Session Skills", result)

    def test_xml_tag_is_available_session_skills(self):
        from agent.runtime import _load_skills_metadata_layer
        result = _load_skills_metadata_layer(self._make_skills("a"))
        self.assertIn("<available_session_skills>", result)
        self.assertIn("</available_session_skills>", result)

    def test_includes_tags_attribute(self):
        from agent.runtime import _load_skills_metadata_layer
        skills = [{"name": "x", "version": "1.0.0", "description": "X", "tags": ["pdf", "rename"]}]
        result = _load_skills_metadata_layer(skills)
        self.assertIn('tags="pdf,rename"', result)

    def test_omits_tags_attribute_when_empty(self):
        from agent.runtime import _load_skills_metadata_layer
        skills = [{"name": "x", "version": "1.0.0", "description": "X", "tags": []}]
        result = _load_skills_metadata_layer(skills)
        self.assertNotIn("tags=", result)

    def test_footer_mentions_skill_load(self):
        from agent.runtime import _load_skills_metadata_layer
        result = _load_skills_metadata_layer(self._make_skills("a"))
        self.assertIn('action="load"', result)
        self.assertIn('name":"pdf-rename', result)

    def test_footer_discourages_skill_list(self):
        from agent.runtime import _load_skills_metadata_layer
        result = _load_skills_metadata_layer(self._make_skills("a"))
        self.assertIn("Do NOT call `skill list`", result)

    def test_returns_empty_for_none(self):
        from agent.runtime import _load_skills_metadata_layer
        self.assertEqual(_load_skills_metadata_layer(None), "")

    def test_returns_empty_for_empty_list(self):
        from agent.runtime import _load_skills_metadata_layer
        self.assertEqual(_load_skills_metadata_layer([]), "")

    def test_deduplicates_by_name(self):
        from agent.runtime import _load_skills_metadata_layer
        skills = [
            {"name": "dup", "version": "1.0.0", "description": "First", "tags": []},
            {"name": "dup", "version": "2.0.0", "description": "Second", "tags": []},
        ]
        result = _load_skills_metadata_layer(skills)
        self.assertEqual(result.count('name="dup"'), 1)


# ── M1: build_system_prompt integration ─────────────────────────────────


class TestBuildSystemPromptSkillsIntegration(unittest.TestCase):
    """Verify build_system_prompt uses the new skills metadata layer."""

    def test_skills_layer_in_prompt(self):
        from agent.runtime import build_system_prompt
        skills = [
            {"name": "pdf-rename", "version": "1.1.0",
             "description": "Split and rename PDF", "tags": ["pdf"]},
        ]
        prompt, diag = build_system_prompt(
            agent_id="build",
            session_dir="/tmp/test",
            user_root="/tmp",
            model_id="test-model",
            session_id="test-session",
            loaded_skills=skills,
        )
        self.assertIn("Available Session Skills", prompt)
        self.assertIn("pdf-rename", prompt)
        self.assertTrue(diag["skills_injected"])
        self.assertEqual(diag["skills_count"], 1)

    def test_no_skills_layer_when_none(self):
        from agent.runtime import build_system_prompt
        prompt, diag = build_system_prompt(
            agent_id="build",
            session_dir="/tmp/test",
            user_root="/tmp",
            model_id="test-model",
            session_id="test-session",
            loaded_skills=None,
        )
        # The role prompt mentions "Available Session Skills" as guidance text,
        # but the actual XML catalog tag should NOT be present.
        self.assertNotIn("<available_session_skills>", prompt)
        self.assertFalse(diag["skills_injected"])


# ── M2: Role Prompt — Natural Recall ────────────────────────────────────


class TestRolePromptNaturalRecall(unittest.TestCase):
    """Verify assistant.md skill section teaches natural recall."""

    @classmethod
    def setUpClass(cls):
        role_path = Path(__file__).parent.parent / "agent" / "prompts" / "roles" / "assistant.md"
        cls.role_text = role_path.read_text(encoding="utf-8")

    def test_mentions_available_session_skills(self):
        self.assertIn("Available Session Skills", self.role_text)

    def test_teaches_check_prompt_metadata_first(self):
        self.assertIn("check the skill metadata already in your prompt", self.role_text)

    def test_teaches_skill_load_directly(self):
        self.assertIn('action="load"', self.role_text)
        self.assertIn('name":"pdf-rename', self.role_text)

    def test_discourages_skill_list_as_first_step(self):
        self.assertIn("Do NOT", self.role_text)
        self.assertIn("skill list", self.role_text)

    def test_preserves_explicit_naming_path(self):
        self.assertIn("user explicitly names a skill", self.role_text)

    def test_no_longer_says_discover_before_loading(self):
        """Old guidance 'Use skill list to discover available skills before loading' should be gone."""
        self.assertNotIn("discover available skills before loading", self.role_text)


# ── M1: Old function removed ────────────────────────────────────────────


class TestOldFunctionRemoved(unittest.TestCase):
    """Verify _fetch_loaded_skill_metadata is no longer in runtime."""

    def test_old_function_not_importable(self):
        import agent.runtime as rt
        self.assertFalse(hasattr(rt, "_fetch_loaded_skill_metadata"))


if __name__ == "__main__":
    unittest.main()
