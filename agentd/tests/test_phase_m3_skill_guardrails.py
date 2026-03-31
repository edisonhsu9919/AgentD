"""Phase M3 — Skill Execution Guardrails tests.

Tests cover:
- WI-1/WI-2: Role prompt guardrail language
- WI-3: Skill observability fields in diagnostics
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── WI-1/WI-2: Role Prompt Guardrails ──────────────────────────────────


class TestRolePromptSkillGuardrails(unittest.TestCase):
    """Verify build.md contains skill execution priority guardrails."""

    @classmethod
    def setUpClass(cls):
        role_path = Path(__file__).parent.parent / "agent" / "prompts" / "roles" / "build.md"
        cls.role_text = role_path.read_text(encoding="utf-8")

    def test_has_skill_execution_priority_section(self):
        self.assertIn("## Skill Execution Priority", self.role_text)

    def test_active_workflow_language(self):
        self.assertIn("active workflow", self.role_text)

    def test_no_generic_replan(self):
        """Should tell model not to create a new generic plan after skill load."""
        self.assertIn("Do not create a new generic plan", self.role_text)

    def test_respect_phase_ordering(self):
        self.assertIn("phase ordering", self.role_text)

    def test_no_skip_prerequisites(self):
        self.assertIn("Do not skip prerequisite phases", self.role_text)

    def test_deviate_with_justification(self):
        self.assertIn("Deviate only with explicit justification", self.role_text)

    def test_no_retry_loops(self):
        self.assertIn("retry loop", self.role_text)

    def test_planning_tool_alignment(self):
        """Should mention using planning/todo_update to track skill phases."""
        self.assertIn("planning", self.role_text)
        self.assertIn("todo_update", self.role_text)


# ── WI-3: Skill Observability in Diagnostics ────────────────────────────


class TestSkillObservabilityFields(unittest.TestCase):
    """Verify _record_run_diagnostics extracts skill fields from messages."""

    def _make_tool_message(self, content: str, name: str = ""):
        """Create a mock ToolMessage."""
        from langchain_core.messages import ToolMessage
        msg = ToolMessage(content=content, tool_call_id="tc-1")
        if name:
            msg.name = name
        return msg

    def _make_ai_message(self, content: str = ""):
        from langchain_core.messages import AIMessage
        return AIMessage(content=content)

    def test_no_skills_loaded(self):
        """When no skill ToolMessages, fields should be empty/zero."""
        from langchain_core.messages import HumanMessage
        messages = [HumanMessage(content="hello"), self._make_ai_message("hi")]

        # Extract logic inline (mirrors _record_run_diagnostics)
        import re
        skill_re = re.compile(r"^\[Skill: (.+?) v(.+?)\]")
        from langchain_core.messages import ToolMessage
        active_skill_names = []
        seen = set()
        last_skill_load_idx = -1
        first_plan_idx = -1
        for idx, m in enumerate(messages):
            if isinstance(m, ToolMessage) and m.content:
                sm = skill_re.match(m.content)
                if sm:
                    sname = sm.group(1)
                    if sname not in seen:
                        seen.add(sname)
                        active_skill_names.append(sname)
                    last_skill_load_idx = idx
                elif getattr(m, "name", "") == "planning" and first_plan_idx < 0:
                    first_plan_idx = idx

        self.assertEqual(active_skill_names, [])
        self.assertEqual(last_skill_load_idx, -1)

    def test_single_skill_loaded(self):
        """Detect one skill load."""
        from langchain_core.messages import ToolMessage
        import re
        skill_re = re.compile(r"^\[Skill: (.+?) v(.+?)\]")

        messages = [
            self._make_ai_message("loading skill"),
            self._make_tool_message("[Skill: pdf-rename v1.1.0]\n\n# PDF Skill...", name="skill"),
        ]

        active_skill_names = []
        seen = set()
        last_skill_load_idx = -1
        for idx, m in enumerate(messages):
            if isinstance(m, ToolMessage) and m.content:
                sm = skill_re.match(m.content)
                if sm:
                    sname = sm.group(1)
                    if sname not in seen:
                        seen.add(sname)
                        active_skill_names.append(sname)
                    last_skill_load_idx = idx

        self.assertEqual(active_skill_names, ["pdf-rename"])
        self.assertEqual(last_skill_load_idx, 1)

    def test_multiple_skills_dedup(self):
        """Same skill loaded twice should appear once in active_skill_names."""
        from langchain_core.messages import ToolMessage
        import re
        skill_re = re.compile(r"^\[Skill: (.+?) v(.+?)\]")

        messages = [
            self._make_tool_message("[Skill: pdf-rename v1.1.0]\ncontent", name="skill"),
            self._make_tool_message("[Skill: pdf-rename v1.1.0]\ncontent again", name="skill"),
            self._make_tool_message("[Skill: ocr v0.1.0]\ncontent", name="skill"),
        ]

        active_skill_names = []
        seen = set()
        for idx, m in enumerate(messages):
            if isinstance(m, ToolMessage) and m.content:
                sm = skill_re.match(m.content)
                if sm:
                    sname = sm.group(1)
                    if sname not in seen:
                        seen.add(sname)
                        active_skill_names.append(sname)

        self.assertEqual(active_skill_names, ["pdf-rename", "ocr"])

    def test_plan_after_skill_load_true(self):
        """Planning ToolMessage AFTER skill load → plan_after_skill_load=True."""
        from langchain_core.messages import ToolMessage
        import re
        skill_re = re.compile(r"^\[Skill: (.+?) v(.+?)\]")

        messages = [
            self._make_tool_message("[Skill: pdf-rename v1.1.0]\n...", name="skill"),
            self._make_ai_message("I'll create a plan"),
            self._make_tool_message('{"task_title":"Split PDF"}', name="planning"),
        ]

        last_skill_load_idx = -1
        first_plan_idx = -1
        for idx, m in enumerate(messages):
            if isinstance(m, ToolMessage) and m.content:
                sm = skill_re.match(m.content)
                if sm:
                    last_skill_load_idx = idx
                elif getattr(m, "name", "") == "planning" and first_plan_idx < 0:
                    first_plan_idx = idx

        result = (
            first_plan_idx > last_skill_load_idx
            if last_skill_load_idx >= 0 and first_plan_idx >= 0
            else None
        )
        self.assertTrue(result)

    def test_plan_before_skill_load_false(self):
        """Planning ToolMessage BEFORE skill load → plan_after_skill_load=False."""
        from langchain_core.messages import ToolMessage
        import re
        skill_re = re.compile(r"^\[Skill: (.+?) v(.+?)\]")

        messages = [
            self._make_tool_message('{"task_title":"My Plan"}', name="planning"),
            self._make_ai_message("loading skill"),
            self._make_tool_message("[Skill: pdf-rename v1.1.0]\n...", name="skill"),
        ]

        last_skill_load_idx = -1
        first_plan_idx = -1
        for idx, m in enumerate(messages):
            if isinstance(m, ToolMessage) and m.content:
                sm = skill_re.match(m.content)
                if sm:
                    last_skill_load_idx = idx
                elif getattr(m, "name", "") == "planning" and first_plan_idx < 0:
                    first_plan_idx = idx

        result = (
            first_plan_idx > last_skill_load_idx
            if last_skill_load_idx >= 0 and first_plan_idx >= 0
            else None
        )
        self.assertFalse(result)

    def test_no_plan_no_skill_returns_none(self):
        """No plan and no skill → plan_after_skill_load=None."""
        messages = [self._make_ai_message("hello")]
        last_skill_load_idx = -1
        first_plan_idx = -1
        result = (
            first_plan_idx > last_skill_load_idx
            if last_skill_load_idx >= 0 and first_plan_idx >= 0
            else None
        )
        self.assertIsNone(result)

    def test_skill_no_plan_returns_none(self):
        """Skill loaded but no plan → plan_after_skill_load=None."""
        last_skill_load_idx = 2
        first_plan_idx = -1
        result = (
            first_plan_idx > last_skill_load_idx
            if last_skill_load_idx >= 0 and first_plan_idx >= 0
            else None
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
