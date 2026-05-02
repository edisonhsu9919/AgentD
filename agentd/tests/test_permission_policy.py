"""Tests for Phase B — session-scoped permission modes.

Covers:
- SessionPolicy schema and defaults
- Policy file I/O (load/save/delete)
- Rule matching (exact_command, any_path_within_session)
- Policy evaluator (manual/autopilot/fsd modes)
- approve-always rule building
- add_rule deduplication and auto-promote
- FSD mode disables HITL interrupt_on
- GET/PATCH /api/sessions/{id}/policy endpoints
"""

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from permission.policy import (
    PolicyRule,
    SessionPolicy,
    add_rule,
    delete_policy,
    evaluate_tool_call,
    load_policy,
    match_rule,
    save_policy,
)


# ── Unit tests: SessionPolicy schema ───────────────────────────────────────


class TestSessionPolicySchema:
    def test_default_policy(self):
        p = SessionPolicy()
        assert p.version == 1
        assert p.mode == "manual"
        assert p.rules == []
        assert p.updated_at != ""

    def test_fsd_mode(self):
        p = SessionPolicy(mode="fsd")
        assert p.mode == "fsd"

    def test_with_rules(self):
        r = PolicyRule(
            tool="bash",
            effect="allow",
            match={"kind": "exact_command", "command": "ls -la"},
        )
        p = SessionPolicy(mode="autopilot", rules=[r])
        assert len(p.rules) == 1
        assert p.rules[0].tool == "bash"

    def test_serialization(self):
        p = SessionPolicy(mode="autopilot")
        d = p.model_dump()
        assert d["mode"] == "autopilot"
        assert d["version"] == 1
        assert isinstance(d["rules"], list)


# ── Unit tests: Policy file I/O ────────────────────────────────────────────


class TestPolicyFileIO:
    def test_load_default_when_absent(self, tmp_path):
        p = load_policy(str(tmp_path))
        assert p.mode == "manual"
        assert p.rules == []

    def test_save_and_load_roundtrip(self, tmp_path):
        policy = SessionPolicy(mode="autopilot", rules=[
            PolicyRule(tool="bash", match={"kind": "exact_command", "command": "pwd"}),
        ])
        save_policy(str(tmp_path), policy)

        loaded = load_policy(str(tmp_path))
        assert loaded.mode == "autopilot"
        assert len(loaded.rules) == 1
        assert loaded.rules[0].match["command"] == "pwd"

    def test_save_creates_agentd_dir(self, tmp_path):
        save_policy(str(tmp_path), SessionPolicy())
        assert os.path.isdir(os.path.join(str(tmp_path), ".agentd"))

    def test_delete_policy_resets(self, tmp_path):
        save_policy(str(tmp_path), SessionPolicy(mode="fsd"))
        delete_policy(str(tmp_path))
        loaded = load_policy(str(tmp_path))
        assert loaded.mode == "manual"  # back to default

    def test_load_handles_corrupt_json(self, tmp_path):
        agentd_dir = os.path.join(str(tmp_path), ".agentd")
        os.makedirs(agentd_dir)
        with open(os.path.join(agentd_dir, "session_policy.json"), "w") as f:
            f.write("{invalid json")
        p = load_policy(str(tmp_path))
        assert p.mode == "manual"  # fallback to default


# ── Unit tests: Rule matching ───────────────────────────────────────────────


class TestRuleMatching:
    def test_exact_command_match(self):
        rule = PolicyRule(tool="bash", match={"kind": "exact_command", "command": "ls -la"})
        assert match_rule(rule, "bash", {"command": "ls -la"}) is True

    def test_exact_command_no_match(self):
        rule = PolicyRule(tool="bash", match={"kind": "exact_command", "command": "ls -la"})
        assert match_rule(rule, "bash", {"command": "rm -rf /"}) is False

    def test_exact_command_strips_whitespace(self):
        rule = PolicyRule(tool="bash", match={"kind": "exact_command", "command": "ls -la"})
        assert match_rule(rule, "bash", {"command": "  ls -la  "}) is True

    def test_wrong_tool_no_match(self):
        rule = PolicyRule(tool="bash", match={"kind": "exact_command", "command": "ls"})
        assert match_rule(rule, "file_write", {"command": "ls"}) is False

    def test_any_path_within_session(self):
        rule = PolicyRule(tool="file_write", match={"kind": "any_path_within_session"})
        assert match_rule(rule, "file_write", {"path": "src/main.py", "content": "x"}) is True

    def test_unknown_kind_no_match(self):
        rule = PolicyRule(tool="bash", match={"kind": "regex_pattern", "pattern": ".*"})
        assert match_rule(rule, "bash", {"command": "ls"}) is False


# ── Unit tests: evaluate_tool_call ──────────────────────────────────────────


class TestEvaluateToolCall:
    def test_manual_always_ask(self):
        policy = SessionPolicy(mode="manual")
        assert evaluate_tool_call(policy, "bash", {"command": "ls"}) == "ask"

    def test_fsd_always_allow(self):
        policy = SessionPolicy(mode="fsd")
        assert evaluate_tool_call(policy, "bash", {"command": "rm -rf /"}) == "allow"
        assert evaluate_tool_call(policy, "file_write", {"path": "x"}) == "allow"
        assert evaluate_tool_call(policy, "some_unknown_tool", {"arg": "val"}) == "allow"

    def test_autopilot_matching_rule_allows(self):
        policy = SessionPolicy(mode="autopilot", rules=[
            PolicyRule(tool="bash", match={"kind": "exact_command", "command": "ls -la"}),
        ])
        assert evaluate_tool_call(policy, "bash", {"command": "ls -la"}) == "allow"

    def test_autopilot_no_match_asks(self):
        policy = SessionPolicy(mode="autopilot", rules=[
            PolicyRule(tool="bash", match={"kind": "exact_command", "command": "ls -la"}),
        ])
        assert evaluate_tool_call(policy, "bash", {"command": "rm test.py"}) == "ask"

    def test_autopilot_file_write_any_path(self):
        policy = SessionPolicy(mode="autopilot", rules=[
            PolicyRule(tool="file_write", match={"kind": "any_path_within_session"}),
        ])
        assert evaluate_tool_call(policy, "file_write", {"path": "foo.txt"}) == "allow"

    def test_manual_ignores_rules(self):
        """In manual mode, even if rules exist (edge case), they are not evaluated."""
        policy = SessionPolicy(mode="manual", rules=[
            PolicyRule(tool="bash", match={"kind": "exact_command", "command": "ls"}),
        ])
        assert evaluate_tool_call(policy, "bash", {"command": "ls"}) == "ask"


# ── Unit tests: add_rule ────────────────────────────────────────────────────


class TestAddRule:
    def test_add_first_rule_promotes_to_autopilot(self):
        policy = SessionPolicy(mode="manual")
        rule = PolicyRule(tool="bash", match={"kind": "exact_command", "command": "ls"})
        result = add_rule(policy, rule)
        assert result.mode == "autopilot"
        assert len(result.rules) == 1

    def test_deduplicates_exact_match(self):
        policy = SessionPolicy(mode="autopilot", rules=[
            PolicyRule(tool="bash", match={"kind": "exact_command", "command": "ls"}),
        ])
        rule = PolicyRule(tool="bash", match={"kind": "exact_command", "command": "ls"})
        result = add_rule(policy, rule)
        assert len(result.rules) == 1  # no duplicate

    def test_different_commands_not_deduplicated(self):
        policy = SessionPolicy(mode="autopilot", rules=[
            PolicyRule(tool="bash", match={"kind": "exact_command", "command": "ls"}),
        ])
        rule = PolicyRule(tool="bash", match={"kind": "exact_command", "command": "pwd"})
        result = add_rule(policy, rule)
        assert len(result.rules) == 2


# ── Unit tests: _build_policy_rule ──────────────────────────────────────────


class TestBuildPolicyRule:
    def test_bash_rule(self):
        from permission.router import _build_policy_rule
        rule = _build_policy_rule("bash", {"command": "ls -la"})
        assert rule is not None
        assert rule.tool == "bash"
        assert rule.match["kind"] == "exact_command"
        assert rule.match["command"] == "ls -la"

    def test_file_write_rule(self):
        from permission.router import _build_policy_rule
        rule = _build_policy_rule("file_write", {"path": "x.py", "content": "hello"})
        assert rule is not None
        assert rule.match["kind"] == "any_path_within_session"

    def test_unknown_tool_returns_none(self):
        from permission.router import _build_policy_rule
        rule = _build_policy_rule("some_other_tool", {"arg": "val"})
        assert rule is None

    def test_bash_empty_command_returns_none(self):
        from permission.router import _build_policy_rule
        rule = _build_policy_rule("bash", {})
        assert rule is None


# ── Unit tests: Evaluator (unified) ────────────────────────────────────────


class TestUnifiedEvaluator:
    def test_autopilot_matching_allows(self):
        from permission.evaluator import evaluate
        policy = SessionPolicy(mode="autopilot", rules=[
            PolicyRule(tool="bash", match={"kind": "exact_command", "command": "ls"}),
        ])
        assert evaluate(policy, "bash", {"command": "ls"}) == "allow"

    def test_manual_falls_through_to_registry(self):
        from permission.evaluator import evaluate
        policy = SessionPolicy(mode="manual")
        # bash default = ask, file_read default = allow
        assert evaluate(policy, "bash", {"command": "ls"}) == "ask"
        assert evaluate(policy, "file_read", {"path": "x"}) == "allow"

    def test_fsd_allows_all(self):
        from permission.evaluator import evaluate
        policy = SessionPolicy(mode="fsd")
        assert evaluate(policy, "bash", {"command": "rm -rf /"}) == "allow"


# ── Unit tests: FSD disables HITL interrupt_on ──────────────────────────────


class TestFsdHitlIntegration:
    def test_fsd_builds_empty_interrupt_on(self, tmp_path):
        """When session is in FSD mode, the HITL middleware should have empty interrupt_on."""
        # Write an FSD policy file
        save_policy(str(tmp_path), SessionPolicy(mode="fsd"))

        from agent.runtime import _build_hitl_middleware
        middleware = _build_hitl_middleware(str(tmp_path))
        # The middleware should have been built with empty interrupt_on
        assert middleware.interrupt_on == {}

    def test_normal_mode_has_interrupt_on(self, tmp_path):
        """Normal (manual/autopilot) mode should keep default interrupt_on."""
        from agent.runtime import _build_hitl_middleware, _HITL_INTERRUPT_ON
        middleware = _build_hitl_middleware(str(tmp_path))
        # Middleware normalizes True → dict, so compare keys
        assert set(middleware.interrupt_on.keys()) == set(_HITL_INTERRUPT_ON.keys())

    def test_no_session_dir_uses_default(self):
        from agent.runtime import _build_hitl_middleware, _HITL_INTERRUPT_ON
        middleware = _build_hitl_middleware("")
        assert set(middleware.interrupt_on.keys()) == set(_HITL_INTERRUPT_ON.keys())


class TestProviderMessageSanitizer:
    def test_known_nonleading_subtask_system_message_converts_to_ai(self):
        from langchain_core.messages import HumanMessage, SystemMessage
        from agent.runtime import _sanitize_nonleading_system_messages

        messages = [
            HumanMessage(content="user asks"),
            SystemMessage(content="[Sub-task completed]\n\nChild summary here"),
        ]

        sanitized, converted = _sanitize_nonleading_system_messages(messages)

        assert converted == 1
        assert len(sanitized) == 2
        assert sanitized[0].type == "human"
        assert sanitized[1].type == "ai"
        assert sanitized[1].content.startswith("[Sub-task completed]")

    def test_unknown_nonleading_system_message_is_dropped(self):
        from langchain_core.messages import HumanMessage, SystemMessage
        from agent.runtime import _sanitize_nonleading_system_messages

        messages = [
            HumanMessage(content="user asks"),
            SystemMessage(content="unexpected hidden instruction"),
        ]

        sanitized, converted = _sanitize_nonleading_system_messages(messages)

        assert converted == 1
        assert len(sanitized) == 1
        assert sanitized[0].type == "human"


# ── Integration tests: GET/PATCH /api/sessions/{id}/policy ──────────────────


class TestPolicyEndpoints:
    @pytest.fixture
    def setup(self, tmp_path):
        sid = uuid.uuid4()
        uid = uuid.uuid4()
        now = datetime.now(timezone.utc)

        session = MagicMock()
        session.id = sid
        session.user_id = uid
        session.status = "idle"
        session.updated_at = now

        user = MagicMock()
        user.id = uid
        user.workspace = str(tmp_path)

        return session, user, sid, tmp_path

    @pytest.fixture
    def client(self, setup):
        session, user, sid, tmp_path = setup
        from api.deps import get_current_user
        from core.database import get_db
        from main import app

        async def override_get_db():
            return AsyncMock()

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = lambda: user

        with patch("session.router.session_svc") as mock_svc:
            mock_svc.get_session = AsyncMock(return_value=session)
            # Mock get_session_dir to return a predictable path
            with patch("workspace.manager.get_session_dir", return_value=str(tmp_path / "sessions" / str(sid))):
                # Create the session dir so policy files can be written
                sdir = tmp_path / "sessions" / str(sid)
                sdir.mkdir(parents=True, exist_ok=True)
                yield TestClient(app), session, sid, sdir

        app.dependency_overrides.clear()

    def test_get_default_policy(self, client):
        test_client, session, sid, sdir = client
        response = test_client.get(f"/api/sessions/{sid}/policy")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["mode"] == "manual"
        assert data["rules"] == []

    def test_patch_to_fsd(self, client):
        test_client, session, sid, sdir = client
        response = test_client.patch(
            f"/api/sessions/{sid}/policy",
            json={"mode": "fsd"},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["mode"] == "fsd"

        # Verify persisted
        response2 = test_client.get(f"/api/sessions/{sid}/policy")
        assert response2.json()["data"]["mode"] == "fsd"

    def test_patch_reset_rules(self, client):
        test_client, session, sid, sdir = client
        # First set to autopilot with a rule
        policy = SessionPolicy(mode="autopilot", rules=[
            PolicyRule(tool="bash", match={"kind": "exact_command", "command": "ls"}),
        ])
        save_policy(str(sdir), policy)

        # Reset rules
        response = test_client.patch(
            f"/api/sessions/{sid}/policy",
            json={"reset_rules": True},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["mode"] == "manual"  # auto-reverts when resetting without explicit mode
        assert data["rules"] == []

    def test_patch_to_manual_from_fsd(self, client):
        test_client, session, sid, sdir = client
        # Set to fsd first
        test_client.patch(f"/api/sessions/{sid}/policy", json={"mode": "fsd"})
        # Switch back to manual
        response = test_client.patch(
            f"/api/sessions/{sid}/policy",
            json={"mode": "manual"},
        )
        assert response.status_code == 200
        assert response.json()["data"]["mode"] == "manual"


@pytest.mark.asyncio
async def test_permission_resume_decisions_are_limited_to_current_hitl_batch(monkeypatch):
    from permission import router as permission_router

    session_id = uuid.uuid4()
    user_id = uuid.uuid4()
    permission_id = uuid.uuid4()
    pr = MagicMock()
    pr.id = permission_id
    pr.session_id = session_id
    pr.tool_call_id = "call_bash"
    pr.tool_name = "bash"
    pr.input = {"command": "pwd"}
    pr.status = "pending"
    session = MagicMock()
    session.id = session_id
    session.user_id = user_id
    session.agent_id = "assistant"
    session.model_id = "test-model"
    current_user = MagicMock()
    current_user.id = user_id
    current_user.workspace = "/tmp/user"
    db = AsyncMock()

    monkeypatch.setattr(
        permission_router.perm_svc,
        "get_permission_request",
        AsyncMock(return_value=pr),
    )
    monkeypatch.setattr(
        permission_router.session_svc,
        "get_session",
        AsyncMock(return_value=session),
    )
    monkeypatch.setattr(
        permission_router.perm_svc,
        "resolve_permission",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        permission_router.perm_svc,
        "count_pending_by_session",
        AsyncMock(return_value=0),
    )
    monkeypatch.setattr(
        permission_router,
        "_current_open_hitl_tool_call_ids",
        AsyncMock(return_value=["call_bash"]),
    )
    monkeypatch.setattr(
        permission_router.perm_svc,
        "get_resolved_by_tool_call_ids",
        AsyncMock(return_value=[
            MagicMock(status="approved", tool_call_id="call_bash"),
        ]),
    )
    get_resolved_by_session = AsyncMock(return_value=[
        MagicMock(status="denied", tool_call_id="historical_call"),
    ])
    monkeypatch.setattr(
        permission_router.perm_svc,
        "get_resolved_by_session",
        get_resolved_by_session,
    )
    mark_batch = AsyncMock(return_value=1)
    monkeypatch.setattr(
        permission_router.perm_svc,
        "mark_resolved_as_resumed_by_tool_call_ids",
        mark_batch,
    )
    enqueue_resume = AsyncMock()
    monkeypatch.setattr("agent.scheduler.enqueue_resume", enqueue_resume)
    monkeypatch.setattr(permission_router.event_bus, "publish", AsyncMock())

    await permission_router._resolve_and_maybe_resume(
        db,
        permission_id,
        "approved",
        current_user,
    )

    enqueue_resume.assert_awaited_once_with(
        db,
        session_id,
        [{"type": "approve"}],
    )
    mark_batch.assert_not_awaited()
    get_resolved_by_session.assert_not_awaited()


# ── Need FastAPI TestClient import ──────────────────────────────────────────
from fastapi.testclient import TestClient
