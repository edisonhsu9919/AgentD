"""v0.4.2 / Phase 1 — Output contract and assistant alias tests."""

import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock


class TestSessionDefaults(unittest.IsolatedAsyncioTestCase):
    async def test_session_create_schema_defaults_to_assistant(self):
        from session.schemas import SessionCreate

        body = SessionCreate(title="Test")

        self.assertEqual(body.agent_id, "assistant")

    async def test_create_session_normalizes_build_to_assistant(self):
        from session.service import create_session

        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()

        session = await create_session(
            db,
            user_id=uuid.uuid4(),
            model_id="test-model",
            agent_id="build",
        )

        self.assertEqual(session.agent_id, "assistant")


class TestAssistantAlias(unittest.TestCase):
    def test_runtime_alias_loads_same_prompt_for_build_and_assistant(self):
        from agent.runtime import _load_role_prompt

        assistant_prompt = _load_role_prompt("assistant")
        build_prompt = _load_role_prompt("build")

        self.assertEqual(build_prompt, assistant_prompt)
        self.assertIn("**assistant** agent", assistant_prompt)

    def test_build_system_prompt_header_shows_assistant_for_legacy_build(self):
        from agent.runtime import build_system_prompt

        with tempfile.TemporaryDirectory() as tmpdir:
            prompt, _ = build_system_prompt(agent_id="build", session_dir=tmpdir)

        self.assertIn("- Agent: assistant", prompt)
        self.assertIn("primary assistant", prompt)


class TestOutputContractRules(unittest.TestCase):
    def test_output_rules_prioritize_markdown_structure(self):
        rules_path = Path(__file__).parent.parent / "agent" / "prompts" / "rules" / "output.md"
        rules_text = rules_path.read_text(encoding="utf-8")

        self.assertIn("Default to Markdown-organized prose", rules_text)
        self.assertIn("prefer short sections, lists, and clear grouping", rules_text)
        self.assertIn("Do not put ordinary prose inside fenced code blocks", rules_text)

    def test_assistant_role_models_user_visible_deliverable(self):
        role_path = Path(__file__).parent.parent / "agent" / "prompts" / "roles" / "assistant.md"
        role_text = role_path.read_text(encoding="utf-8")

        self.assertIn("The text the user sees is part of the product output", role_text)
        self.assertIn("Default to Markdown-organized responses", role_text)
        self.assertIn("Do not put normal explanatory prose inside fenced code blocks", role_text)

    def test_assistant_role_keeps_knowledge_tool_guidance(self):
        role_path = Path(__file__).parent.parent / "agent" / "prompts" / "roles" / "assistant.md"
        role_text = role_path.read_text(encoding="utf-8")

        self.assertIn("### Knowledge Tools", role_text)
        self.assertIn("knowledge_catalog", role_text)
        self.assertIn("### Parallel Tool Calls", role_text)
