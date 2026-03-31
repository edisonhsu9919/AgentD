"""Phase M4-A — Skill Runtime Env Helpers tests.

Tests cover:
- Schema and file structure
- read_skill_envs (empty, missing, valid, malformed)
- register_skill_scripts (single skill, multi skill, update)
- resolve_env_for_script (match, no match, missing env dir)
- resolve_env_for_command (match, no match, multi entries)
- get_catalog_skill_env_bin (exists, missing)
- _normalize_rel_path
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestReadSkillEnvs(unittest.TestCase):

    def test_returns_empty_for_missing_file(self):
        from skills.env import read_skill_envs
        with tempfile.TemporaryDirectory() as d:
            result = read_skill_envs(d)
            self.assertEqual(result["version"], 1)
            self.assertEqual(result["entries"], {})

    def test_returns_empty_for_malformed_json(self):
        from skills.env import read_skill_envs
        with tempfile.TemporaryDirectory() as d:
            agentd = os.path.join(d, ".agentd")
            os.makedirs(agentd)
            with open(os.path.join(agentd, "skill_envs.json"), "w") as f:
                f.write("not json")
            result = read_skill_envs(d)
            self.assertEqual(result["entries"], {})

    def test_returns_empty_for_wrong_schema(self):
        from skills.env import read_skill_envs
        with tempfile.TemporaryDirectory() as d:
            agentd = os.path.join(d, ".agentd")
            os.makedirs(agentd)
            with open(os.path.join(agentd, "skill_envs.json"), "w") as f:
                json.dump({"foo": "bar"}, f)
            result = read_skill_envs(d)
            self.assertEqual(result["entries"], {})

    def test_reads_valid_file(self):
        from skills.env import read_skill_envs
        with tempfile.TemporaryDirectory() as d:
            agentd = os.path.join(d, ".agentd")
            os.makedirs(agentd)
            data = {
                "version": 1,
                "entries": {
                    "scripts/foo.py": {
                        "skill_name": "test",
                        "skill_version": "1.0.0",
                        "env_bin": "/some/path",
                    }
                },
            }
            with open(os.path.join(agentd, "skill_envs.json"), "w") as f:
                json.dump(data, f)
            result = read_skill_envs(d)
            self.assertEqual(len(result["entries"]), 1)
            self.assertEqual(result["entries"]["scripts/foo.py"]["skill_name"], "test")


class TestRegisterSkillScripts(unittest.TestCase):

    def test_creates_file_if_not_exists(self):
        from skills.env import register_skill_scripts, read_skill_envs
        with tempfile.TemporaryDirectory() as d:
            register_skill_scripts(
                d, "pdf-rename", "1.1.0", "/env/bin",
                ["scripts/a.py", "scripts/b.py"],
            )
            result = read_skill_envs(d)
            self.assertEqual(len(result["entries"]), 2)
            self.assertEqual(result["entries"]["scripts/a.py"]["skill_name"], "pdf-rename")
            self.assertEqual(result["entries"]["scripts/b.py"]["env_bin"], "/env/bin")

    def test_preserves_entries_from_other_skills(self):
        from skills.env import register_skill_scripts, read_skill_envs
        with tempfile.TemporaryDirectory() as d:
            # Register skill 1
            register_skill_scripts(
                d, "pdf-rename", "1.1.0", "/env1/bin",
                ["scripts/split.py"],
            )
            # Register skill 2
            register_skill_scripts(
                d, "ocr", "0.1.0", "/env2/bin",
                ["scripts/scan.py"],
            )
            result = read_skill_envs(d)
            self.assertEqual(len(result["entries"]), 2)
            self.assertEqual(result["entries"]["scripts/split.py"]["skill_name"], "pdf-rename")
            self.assertEqual(result["entries"]["scripts/scan.py"]["skill_name"], "ocr")

    def test_updates_entries_for_same_skill(self):
        from skills.env import register_skill_scripts, read_skill_envs
        with tempfile.TemporaryDirectory() as d:
            register_skill_scripts(
                d, "pdf-rename", "1.0.0", "/old/bin",
                ["scripts/a.py"],
            )
            register_skill_scripts(
                d, "pdf-rename", "1.1.0", "/new/bin",
                ["scripts/a.py"],
            )
            result = read_skill_envs(d)
            self.assertEqual(len(result["entries"]), 1)
            self.assertEqual(result["entries"]["scripts/a.py"]["skill_version"], "1.1.0")
            self.assertEqual(result["entries"]["scripts/a.py"]["env_bin"], "/new/bin")

    def test_normalizes_paths(self):
        from skills.env import register_skill_scripts, read_skill_envs
        with tempfile.TemporaryDirectory() as d:
            register_skill_scripts(
                d, "test", "1.0.0", "/env/bin",
                ["./scripts/foo.py"],
            )
            result = read_skill_envs(d)
            self.assertIn("scripts/foo.py", result["entries"])
            self.assertNotIn("./scripts/foo.py", result["entries"])


class TestResolveEnvForScript(unittest.TestCase):

    def test_returns_default_when_no_file(self):
        from skills.env import resolve_env_for_script
        with tempfile.TemporaryDirectory() as d:
            result = resolve_env_for_script(d, "scripts/foo.py", "/default/bin")
            self.assertEqual(result, "/default/bin")

    def test_returns_skill_env_when_matched_and_exists(self):
        from skills.env import register_skill_scripts, resolve_env_for_script
        with tempfile.TemporaryDirectory() as d:
            # Create a fake env_bin dir
            fake_env = os.path.join(d, "fake_env", "bin")
            os.makedirs(fake_env)
            register_skill_scripts(
                d, "pdf-rename", "1.1.0", fake_env,
                ["scripts/split.py"],
            )
            result = resolve_env_for_script(d, "scripts/split.py", "/default/bin")
            self.assertEqual(result, fake_env)

    def test_returns_default_when_env_dir_missing(self):
        from skills.env import register_skill_scripts, resolve_env_for_script
        with tempfile.TemporaryDirectory() as d:
            register_skill_scripts(
                d, "pdf-rename", "1.1.0", "/nonexistent/bin",
                ["scripts/split.py"],
            )
            result = resolve_env_for_script(d, "scripts/split.py", "/default/bin")
            self.assertEqual(result, "/default/bin")

    def test_returns_default_for_unregistered_script(self):
        from skills.env import register_skill_scripts, resolve_env_for_script
        with tempfile.TemporaryDirectory() as d:
            fake_env = os.path.join(d, "fake_env", "bin")
            os.makedirs(fake_env)
            register_skill_scripts(
                d, "pdf-rename", "1.1.0", fake_env,
                ["scripts/split.py"],
            )
            result = resolve_env_for_script(d, "scripts/other.py", "/default/bin")
            self.assertEqual(result, "/default/bin")


class TestResolveEnvForCommand(unittest.TestCase):

    def test_returns_default_when_no_entries(self):
        from skills.env import resolve_env_for_command
        with tempfile.TemporaryDirectory() as d:
            result = resolve_env_for_command(d, "python foo.py", "/default/bin")
            self.assertEqual(result, "/default/bin")

    def test_matches_script_in_command(self):
        from skills.env import register_skill_scripts, resolve_env_for_command
        with tempfile.TemporaryDirectory() as d:
            fake_env = os.path.join(d, "fake_env", "bin")
            os.makedirs(fake_env)
            register_skill_scripts(
                d, "pdf-rename", "1.1.0", fake_env,
                ["scripts/pdf_extract_text.py"],
            )
            cmd = "python scripts/pdf_extract_text.py claim.pdf --chars 300"
            result = resolve_env_for_command(d, cmd, "/default/bin")
            self.assertEqual(result, fake_env)

    def test_no_match_for_unrelated_command(self):
        from skills.env import register_skill_scripts, resolve_env_for_command
        with tempfile.TemporaryDirectory() as d:
            fake_env = os.path.join(d, "fake_env", "bin")
            os.makedirs(fake_env)
            register_skill_scripts(
                d, "pdf-rename", "1.1.0", fake_env,
                ["scripts/split.py"],
            )
            result = resolve_env_for_command(d, "ls -la", "/default/bin")
            self.assertEqual(result, "/default/bin")

    def test_multi_skill_correct_resolution(self):
        from skills.env import register_skill_scripts, resolve_env_for_command
        with tempfile.TemporaryDirectory() as d:
            env1 = os.path.join(d, "env1", "bin")
            env2 = os.path.join(d, "env2", "bin")
            os.makedirs(env1)
            os.makedirs(env2)
            register_skill_scripts(d, "pdf-rename", "1.1.0", env1, ["scripts/split.py"])
            register_skill_scripts(d, "ocr", "0.1.0", env2, ["scripts/scan.py"])

            r1 = resolve_env_for_command(d, "python scripts/split.py plan.json", "/default")
            self.assertEqual(r1, env1)

            r2 = resolve_env_for_command(d, "python scripts/scan.py img.png", "/default")
            self.assertEqual(r2, env2)

            r3 = resolve_env_for_command(d, "python my_script.py", "/default")
            self.assertEqual(r3, "/default")


class TestGetCatalogSkillEnvBin(unittest.TestCase):

    def test_returns_path_when_exists(self):
        from skills.env import get_catalog_skill_env_bin
        with tempfile.TemporaryDirectory() as d:
            # Monkey-patch settings.workspace_root
            from core.config import settings
            orig = settings.workspace_root
            try:
                settings.workspace_root = d
                env_bin = os.path.join(d, "_catalog", "skills", "test-skill", "1.0.0", ".venv", "bin")
                os.makedirs(env_bin)
                result = get_catalog_skill_env_bin("test-skill", "1.0.0")
                self.assertEqual(result, env_bin)
            finally:
                settings.workspace_root = orig

    def test_returns_none_when_missing(self):
        from skills.env import get_catalog_skill_env_bin
        with tempfile.TemporaryDirectory() as d:
            from core.config import settings
            orig = settings.workspace_root
            try:
                settings.workspace_root = d
                result = get_catalog_skill_env_bin("nonexistent", "1.0.0")
                self.assertIsNone(result)
            finally:
                settings.workspace_root = orig


class TestNormalizeRelPath(unittest.TestCase):

    def test_strips_leading_dot_slash(self):
        from skills.env import _normalize_rel_path
        self.assertEqual(_normalize_rel_path("./scripts/foo.py"), "scripts/foo.py")

    def test_preserves_normal_path(self):
        from skills.env import _normalize_rel_path
        self.assertEqual(_normalize_rel_path("scripts/foo.py"), "scripts/foo.py")

    def test_collapses_double_slash(self):
        from skills.env import _normalize_rel_path
        self.assertEqual(_normalize_rel_path("scripts//foo.py"), "scripts/foo.py")

    def test_simple_filename(self):
        from skills.env import _normalize_rel_path
        self.assertEqual(_normalize_rel_path("foo.py"), "foo.py")


if __name__ == "__main__":
    unittest.main()
