"""Phase L — Runtime Continuity & Task Control tests.

Tests cover:
- Migration 011 (diagnostics field)
- Incremental tool history persistence
- Waiting boundary persistence
- Waiting boundary diagnostics (audit fix)
- Abort preemption in streaming
- Prompt diagnostics collection
- Run history API
- Plan reset (existing endpoint verification)
- Unified cancel-task endpoint (audit fix)
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── L1: Migration 011 ─────────────────────────────────────────────────────


class TestMigration011(unittest.TestCase):
    """Verify migration 011 exists and schema version is updated."""

    def test_migration_file_exists(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", "db", "alembic", "versions",
            "011_add_agent_runs_diagnostics.py",
        )
        self.assertTrue(os.path.isfile(path))

    def test_migration_revision(self):
        from db.alembic.versions import _011_add_agent_runs_diagnostics as m011
        self.assertEqual(m011.revision, "011")
        self.assertEqual(m011.down_revision, "010")

    def test_expected_schema_version(self):
        from main import EXPECTED_SCHEMA_VERSION
        assert EXPECTED_SCHEMA_VERSION == "015"

    def test_agent_run_model_has_diagnostics(self):
        from agent.run_models import AgentRun
        self.assertTrue(hasattr(AgentRun, "diagnostics"))


# ── Importability helper ──────────────────────────────────────────────────


def _import_migration_011():
    """Import migration 011 dynamically (module name starts with digit)."""
    import importlib
    path = os.path.join(
        os.path.dirname(__file__), "..", "db", "alembic", "versions",
        "011_add_agent_runs_diagnostics.py",
    )
    spec = importlib.util.spec_from_file_location("_011_add_agent_runs_diagnostics", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

# Make migration importable for test above
sys.modules["db.alembic.versions._011_add_agent_runs_diagnostics"] = _import_migration_011()


# ── L2: Incremental persistence ──────────────────────────────────────────


class TestIncrementalPersistence(unittest.TestCase):
    """Verify _persist_message_incremental exists and handles message types."""

    def test_function_exists(self):
        from agent.executor import _persist_message_incremental
        self.assertTrue(callable(_persist_message_incremental))

    def test_function_is_async(self):
        import asyncio
        from agent.executor import _persist_message_incremental
        self.assertTrue(asyncio.iscoroutinefunction(_persist_message_incremental))


# ── L3: Waiting boundary persistence ─────────────────────────────────────


class TestWaitingBoundaryPersistence(unittest.TestCase):
    """Verify _handle_interrupt calls _persist_messages before waiting."""

    def test_handle_interrupt_calls_persist_before_waiting(self):
        """Check that the standard ask flow in _handle_interrupt contains
        a _persist_messages call before _update_db_status('waiting')."""
        import inspect
        from agent.executor import _handle_interrupt
        source = inspect.getsource(_handle_interrupt)
        # _persist_messages should appear before "waiting" status update
        persist_idx = source.find("_persist_messages")
        waiting_idx = source.find('"waiting"')
        self.assertGreater(persist_idx, -1, "_persist_messages not found in _handle_interrupt")
        self.assertGreater(waiting_idx, -1, "waiting status not found in _handle_interrupt")
        self.assertLess(persist_idx, waiting_idx,
                        "_persist_messages should be called before setting waiting status")

    def test_handle_interrupt_records_diagnostics_before_waiting(self):
        """Audit fix: _record_run_diagnostics must be called before waiting status.

        Without this, waiting runs have diagnostics=null, breaking prompt
        continuity analysis on the most critical code path."""
        import inspect
        from agent.executor import _handle_interrupt
        source = inspect.getsource(_handle_interrupt)
        diag_idx = source.find("_record_run_diagnostics")
        waiting_idx = source.find('"waiting"')
        self.assertGreater(diag_idx, -1,
                           "_record_run_diagnostics not found in _handle_interrupt")
        self.assertLess(diag_idx, waiting_idx,
                        "_record_run_diagnostics should be called before setting waiting status")


# ── L4: Abort preemption ─────────────────────────────────────────────────


class TestAbortPreemption(unittest.TestCase):
    """Verify abort check is inside _stream_and_translate."""

    def test_stream_and_translate_accepts_check_abort(self):
        import inspect
        from agent.executor import _stream_and_translate
        sig = inspect.signature(_stream_and_translate)
        self.assertIn("check_abort", sig.parameters)

    def test_stream_and_translate_returns_bool(self):
        """Verify return type annotation includes bool."""
        import inspect
        from agent.executor import _stream_and_translate
        sig = inspect.signature(_stream_and_translate)
        ret = sig.return_annotation
        self.assertEqual(ret, bool)

    def test_stream_and_translate_checks_abort_in_body(self):
        import inspect
        from agent.executor import _stream_and_translate
        source = inspect.getsource(_stream_and_translate)
        self.assertIn("check_abort", source)
        self.assertIn("return True", source)  # abort path returns True


# ── L5: Plan reset (existing endpoint verification) ──────────────────────


class TestPlanResetEndpoint(unittest.TestCase):
    """Verify DELETE /sessions/{id}/task-plan endpoint exists."""

    def test_delete_task_plan_endpoint_exists(self):
        from session.router import router
        paths = [r.path for r in router.routes if hasattr(r, "path")]
        self.assertIn("/{session_id}/task-plan", paths)

    def test_delete_task_plan_methods(self):
        from session.router import router
        for route in router.routes:
            if hasattr(route, "path") and route.path == "/{session_id}/task-plan":
                if "DELETE" in route.methods:
                    return
        self.fail("DELETE method not found on /{session_id}/task-plan")


# ── L6: Prompt diagnostics ───────────────────────────────────────────────


class TestPromptDiagnostics(unittest.TestCase):
    """Verify build_system_prompt returns diagnostics and _record_run_diagnostics exists."""

    def test_build_system_prompt_returns_tuple(self):
        from agent.runtime import build_system_prompt
        import inspect
        sig = inspect.signature(build_system_prompt)
        ret = sig.return_annotation
        self.assertEqual(ret, tuple[str, dict])

    def test_build_system_prompt_diagnostics_keys(self):
        """build_system_prompt should return diagnostics with expected keys."""
        from agent.runtime import build_system_prompt
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            prompt, diag = build_system_prompt(
                agent_id="build",
                session_dir=tmpdir,
            )
        self.assertIsInstance(prompt, str)
        self.assertIsInstance(diag, dict)
        self.assertIn("system_prompt_chars", diag)
        self.assertIn("system_prompt_layers", diag)
        self.assertIn("task_plan_injected", diag)
        self.assertIn("skills_injected", diag)
        self.assertIn("skills_count", diag)
        self.assertGreater(diag["system_prompt_chars"], 0)

    def test_task_plan_no_longer_injected(self):
        """Phase L/N2: task_plan not injected in fresh session (no compaction)."""
        from agent.runtime import build_system_prompt
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            _, diag = build_system_prompt(agent_id="build", session_dir=tmpdir)
        self.assertFalse(diag["task_plan_injected"])

    def test_diagnostics_layer_sizes(self):
        from agent.runtime import build_system_prompt
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            _, diag = build_system_prompt(agent_id="build", session_dir=tmpdir)
        layers = diag["system_prompt_layers"]
        self.assertIn("header", layers)
        self.assertIn("role", layers)
        self.assertIn("rules", layers)
        self.assertIn("task_plan", layers)
        self.assertEqual(layers["task_plan"], 0)  # Plan no longer in system prompt
        self.assertIn("skills", layers)
        self.assertGreater(layers["header"], 0)

    def test_skills_metadata_layer_format(self):
        """Phase L: skills layer should use metadata format, not full content."""
        from agent.runtime import build_system_prompt
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            prompt, diag = build_system_prompt(
                agent_id="build", session_dir=tmpdir,
                loaded_skills=[
                    {"name": "code_review", "version": "1.0.0", "description": "Review code"},
                ],
            )
        self.assertTrue(diag["skills_injected"])
        self.assertEqual(diag["skills_count"], 1)
        # Should contain XML metadata, not full SKILL.md content
        self.assertIn("<available_session_skills>", prompt)
        self.assertIn('name="code_review"', prompt)
        self.assertIn("Review code", prompt)

    def test_record_run_diagnostics_exists(self):
        from agent.executor import _record_run_diagnostics
        import asyncio
        self.assertTrue(asyncio.iscoroutinefunction(_record_run_diagnostics))

    def test_update_diagnostics_scheduler_function(self):
        from agent.scheduler import update_diagnostics
        import asyncio
        self.assertTrue(asyncio.iscoroutinefunction(update_diagnostics))


# ── L7: Run History API ──────────────────────────────────────────────────


class TestRunHistoryAPI(unittest.TestCase):
    """Verify GET /sessions/{id}/runs endpoint exists."""

    def test_runs_endpoint_exists(self):
        from session.router import router
        paths = [r.path for r in router.routes if hasattr(r, "path")]
        self.assertIn("/{session_id}/runs", paths)

    def test_runs_endpoint_is_get(self):
        from session.router import router
        for route in router.routes:
            if hasattr(route, "path") and route.path == "/{session_id}/runs":
                if "GET" in route.methods:
                    return
        self.fail("GET method not found on /{session_id}/runs")

    def test_runs_endpoint_requires_admin(self):
        """The runs endpoint should use require_admin dependency."""
        import inspect
        from session.router import list_session_runs
        source = inspect.getsource(list_session_runs)
        self.assertIn("require_admin", source)


# ── L8: Unified cancel-task endpoint (audit fix) ────────────────────────


class TestCancelTaskEndpoint(unittest.TestCase):
    """Verify DELETE /sessions/{id}/cancel-task endpoint exists and is unified."""

    def test_cancel_task_endpoint_exists(self):
        from session.router import router
        paths = [r.path for r in router.routes if hasattr(r, "path")]
        self.assertIn("/{session_id}/cancel-task", paths)

    def test_cancel_task_is_delete(self):
        from session.router import router
        for route in router.routes:
            if hasattr(route, "path") and route.path == "/{session_id}/cancel-task":
                if "DELETE" in route.methods:
                    return
        self.fail("DELETE method not found on /{session_id}/cancel-task")

    def test_cancel_task_handler_clears_plan(self):
        """cancel_task handler should remove task_plan.json."""
        import inspect
        from session.router import cancel_task
        source = inspect.getsource(cancel_task)
        self.assertIn("task_plan.json", source)
        self.assertIn("os.remove", source)

    def test_cancel_task_handler_aborts_run(self):
        """cancel_task handler should invoke abort/cancel logic."""
        import inspect
        from session.router import cancel_task
        source = inspect.getsource(cancel_task)
        self.assertIn("cancel_queued_runs", source)
        self.assertIn("enqueue_abort", source)

    def test_cancel_task_handler_resets_waiting(self):
        """cancel_task should directly reset waiting sessions to idle."""
        import inspect
        from session.router import cancel_task
        source = inspect.getsource(cancel_task)
        self.assertIn("cancel_pending_by_session", source)
        # Should publish status_change for SSE listeners
        self.assertIn("status_change", source)


# ── L9: Prompt assembly trace (§12.4 + §12.6) ─────────────────────────


class TestPromptAssemblyTrace(unittest.TestCase):
    """Verify build_system_prompt returns prompt_assembly_order diagnostics."""

    def test_assembly_order_present(self):
        from agent.runtime import build_system_prompt
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            _, diag = build_system_prompt(agent_id="build", session_dir=tmpdir)
        self.assertIn("prompt_assembly_order", diag)
        order = diag["prompt_assembly_order"]
        self.assertIsInstance(order, list)
        self.assertGreater(len(order), 0)

    def test_assembly_order_layer_names(self):
        """Each entry should have name, chars, injected fields."""
        from agent.runtime import build_system_prompt
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            _, diag = build_system_prompt(agent_id="build", session_dir=tmpdir)
        order = diag["prompt_assembly_order"]
        expected_names = ["role", "rules", "header", "skills", "task_plan", "compaction_context"]
        actual_names = [entry["name"] for entry in order]
        self.assertEqual(actual_names, expected_names)
        for entry in order:
            self.assertIn("chars", entry)
            self.assertIn("injected", entry)
            self.assertIsInstance(entry["chars"], int)
            self.assertIsInstance(entry["injected"], bool)

    def test_assembly_order_task_plan_not_injected(self):
        """Task plan should never be injected (Phase L prompt strategy)."""
        from agent.runtime import build_system_prompt
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            _, diag = build_system_prompt(agent_id="build", session_dir=tmpdir)
        order = diag["prompt_assembly_order"]
        plan_entry = [e for e in order if e["name"] == "task_plan"][0]
        self.assertFalse(plan_entry["injected"])
        self.assertEqual(plan_entry["chars"], 0)

    def test_assembly_order_skills_injected(self):
        """Skills layer should be injected when loaded_skills provided."""
        from agent.runtime import build_system_prompt
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            _, diag = build_system_prompt(
                agent_id="build", session_dir=tmpdir,
                loaded_skills=[{"name": "test", "version": "1.0", "description": "Test"}],
            )
        order = diag["prompt_assembly_order"]
        skills_entry = [e for e in order if e["name"] == "skills"][0]
        self.assertTrue(skills_entry["injected"])
        self.assertGreater(skills_entry["chars"], 0)


# ── L10: Checkpoint composition in diagnostics (§12.6) ────────────────


class TestCheckpointComposition(unittest.TestCase):
    """Verify _record_run_diagnostics includes checkpoint_composition."""

    def test_record_run_diagnostics_source_has_composition(self):
        """Source code should reference checkpoint_composition."""
        import inspect
        from agent.executor import _record_run_diagnostics
        source = inspect.getsource(_record_run_diagnostics)
        self.assertIn("checkpoint_composition", source)

    def test_record_run_diagnostics_includes_run_type(self):
        """Source code should include run_type in diagnostics."""
        import inspect
        from agent.executor import _record_run_diagnostics
        source = inspect.getsource(_record_run_diagnostics)
        self.assertIn("run_type", source)

    def test_record_run_diagnostics_includes_context_window(self):
        """Source code should reference context_window_limit."""
        import inspect
        from agent.executor import _record_run_diagnostics
        source = inspect.getsource(_record_run_diagnostics)
        self.assertIn("context_window_limit", source)
        self.assertIn("context_usage_ratio", source)


# ── L11: Migration 012 + context window in model config (§12.5) ───────


class TestMigration012(unittest.TestCase):
    """Verify migration 012 exists and model config has context_window."""

    def test_migration_file_exists(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", "db", "alembic", "versions",
            "012_add_model_config_context_window.py",
        )
        self.assertTrue(os.path.isfile(path))

    def test_migration_revision(self):
        import importlib
        path = os.path.join(
            os.path.dirname(__file__), "..", "db", "alembic", "versions",
            "012_add_model_config_context_window.py",
        )
        spec = importlib.util.spec_from_file_location("_012", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertEqual(mod.revision, "012")
        self.assertEqual(mod.down_revision, "011")

    def test_expected_schema_version(self):
        from main import EXPECTED_SCHEMA_VERSION
        assert EXPECTED_SCHEMA_VERSION == "015"

    def test_model_config_has_context_window(self):
        from model_config.models import ModelConfig
        self.assertTrue(hasattr(ModelConfig, "context_window"))

    def test_resolved_model_config_has_context_window(self):
        from model_config.service import ResolvedModelConfig
        import dataclasses
        fields = {f.name for f in dataclasses.fields(ResolvedModelConfig)}
        self.assertIn("context_window", fields)

    def test_resolved_env_fallback_has_context_window(self):
        """Env fallback should populate context_window from settings."""
        from model_config.service import ResolvedModelConfig
        from core.config import settings
        resolved = ResolvedModelConfig(
            source="env_fallback",
            name="test",
            base_url="http://localhost",
            api_key="",
            model_id="test",
            context_window=settings.context_window_tokens,
        )
        self.assertEqual(resolved.context_window, settings.context_window_tokens)


# ── L12: context_window API exposure (Codex audit fix) ────────────────


class TestContextWindowAPI(unittest.TestCase):
    """Verify context_window is exposed through model-config API schemas."""

    def test_create_schema_has_context_window(self):
        from model_config.schemas import ModelConfigCreate
        body = ModelConfigCreate(
            name="Test", base_url="http://localhost", model_id="m1",
            context_window=32768,
        )
        self.assertEqual(body.context_window, 32768)

    def test_create_schema_context_window_optional(self):
        from model_config.schemas import ModelConfigCreate
        body = ModelConfigCreate(
            name="Test", base_url="http://localhost", model_id="m1",
        )
        self.assertIsNone(body.context_window)

    def test_update_schema_has_context_window(self):
        from model_config.schemas import ModelConfigUpdate
        body = ModelConfigUpdate(context_window=65536)
        self.assertEqual(body.context_window, 65536)

    def test_response_schema_has_context_window(self):
        from model_config.schemas import ModelConfigResponse
        fields = ModelConfigResponse.model_fields
        self.assertIn("context_window", fields)

    def test_runtime_model_config_endpoint_returns_context_window(self):
        """Verify runtime endpoint source code includes context_window."""
        import inspect
        from model_config.router import _build_runtime_model_config_payload
        source = inspect.getsource(_build_runtime_model_config_payload)
        self.assertIn("context_window", source)

    def test_runtime_diagnostics_endpoint_returns_context_window(self):
        """Verify diagnostics endpoint source code includes context_window."""
        import inspect
        from model_config.router import get_runtime_diagnostics
        source = inspect.getsource(get_runtime_diagnostics)
        self.assertIn("context_window", source)


# ── L13: Call-level token observation (Codex audit fix) ───────────────


class TestCallLevelTokenObservation(unittest.TestCase):
    """Verify call-level (not run-level) token extraction exists."""

    def test_extract_last_call_usage_exists(self):
        from agent.executor import _extract_last_call_usage
        self.assertTrue(callable(_extract_last_call_usage))

    def test_extract_last_call_usage_empty(self):
        from agent.executor import _extract_last_call_usage
        result = _extract_last_call_usage([])
        self.assertEqual(result["prompt_tokens"], 0)
        self.assertEqual(result["completion_tokens"], 0)

    def test_extract_last_call_usage_returns_last_ai(self):
        """Should return usage from the LAST AIMessage, not accumulated."""
        from agent.executor import _extract_last_call_usage
        from langchain_core.messages import AIMessage

        msg1 = AIMessage(content="first")
        msg1.usage_metadata = {"input_tokens": 100, "output_tokens": 50}
        msg2 = AIMessage(content="second")
        msg2.usage_metadata = {"input_tokens": 200, "output_tokens": 80}

        result = _extract_last_call_usage([msg1, msg2])
        # Should be from msg2 (last), not sum
        self.assertEqual(result["prompt_tokens"], 200)
        self.assertEqual(result["completion_tokens"], 80)
        self.assertEqual(result["total_tokens"], 280)

    def test_extract_last_call_usage_cache_fields(self):
        """Should include cache_read_tokens and cache_creation_tokens."""
        from agent.executor import _extract_last_call_usage
        from langchain_core.messages import AIMessage

        msg = AIMessage(content="test")
        msg.usage_metadata = {
            "input_tokens": 300,
            "output_tokens": 100,
            "cache_read_input_tokens": 250,
            "cache_creation_input_tokens": 50,
        }
        result = _extract_last_call_usage([msg])
        self.assertEqual(result["cache_read_tokens"], 250)
        self.assertEqual(result["cache_creation_tokens"], 50)

    def test_diagnostics_includes_last_call_fields(self):
        """Source code should include last_call_* fields in diagnostics."""
        import inspect
        from agent.executor import _record_run_diagnostics
        source = inspect.getsource(_record_run_diagnostics)
        self.assertIn("last_call_prompt_tokens", source)
        self.assertIn("last_call_completion_tokens", source)
        self.assertIn("last_call_cache_read_tokens", source)

    def test_context_usage_ratio_uses_last_call(self):
        """context_usage_ratio should use last_call_prompt_tokens, not accumulated."""
        import inspect
        from agent.executor import _record_run_diagnostics
        source = inspect.getsource(_record_run_diagnostics)
        # The ratio computation should use last_call, not token_usage
        self.assertIn('last_call["prompt_tokens"]', source)


# ── L14: Provider context_window discovery ─────────────────────────────


class TestProviderContextWindowDiscovery(unittest.TestCase):
    """Verify discover_provider_context_window function."""

    def test_function_exists(self):
        from model_config.service import discover_provider_context_window
        import asyncio
        self.assertTrue(asyncio.iscoroutinefunction(discover_provider_context_window))

    def test_resolver_calls_discovery(self):
        """resolve_active_model_config should call discover_provider_context_window."""
        import inspect
        from model_config.service import resolve_active_model_config
        source = inspect.getsource(resolve_active_model_config)
        self.assertIn("discover_provider_context_window", source)

    def test_resolver_prefers_discovered_over_manual(self):
        """Resolver docstring should document provider-first priority."""
        import inspect
        from model_config.service import resolve_active_model_config
        doc = inspect.getdoc(resolve_active_model_config)
        self.assertIn("Provider", doc)

    def test_discovery_reads_nested_meta(self):
        """discover_provider_context_window should read meta.n_ctx_train (llama.cpp)."""
        import inspect
        from model_config.service import _fetch_provider_context_window
        source = inspect.getsource(_fetch_provider_context_window)
        self.assertIn("n_ctx_train", source)
        self.assertIn('m.get("meta")', source)

    def test_discovery_is_cached(self):
        """discover_provider_context_window should use TTL cache."""
        import inspect
        from model_config.service import discover_provider_context_window
        source = inspect.getsource(discover_provider_context_window)
        self.assertIn("_provider_cw_cache", source)
        self.assertIn("_PROVIDER_CW_TTL", source)

    def test_invalidate_cache_exists(self):
        from model_config.service import invalidate_provider_cache
        self.assertTrue(callable(invalidate_provider_cache))

    def test_router_invalidates_on_config_change(self):
        """Router should call invalidate_provider_cache on create/update/set-default."""
        import inspect
        from model_config.router import create_model_config, update_model_config, set_default_model_config
        for fn in (create_model_config, update_model_config, set_default_model_config):
            source = inspect.getsource(fn)
            self.assertIn("invalidate_provider_cache", source, f"{fn.__name__} missing cache invalidation")


# ── L15: Runtime context occupancy for frontend ──────────────────────


class TestRuntimeContextOccupancy(unittest.TestCase):
    """Verify RuntimeResponse carries context occupancy fields."""

    def test_runtime_response_has_context_fields(self):
        from session.schemas import RuntimeResponse
        fields = RuntimeResponse.model_fields
        self.assertIn("last_call_prompt_tokens", fields)
        self.assertIn("last_call_completion_tokens", fields)
        self.assertIn("context_window_limit", fields)
        self.assertIn("context_usage_ratio", fields)

    def test_runtime_response_defaults(self):
        """Context fields should default to 0/None (no data yet)."""
        from session.schemas import RuntimeResponse
        from datetime import datetime, timezone
        import uuid
        r = RuntimeResponse(
            session_id=uuid.uuid4(),
            status="idle",
            last_message_seq=0,
            pending_permissions_count=0,
            resumable=False,
            updated_at=datetime.now(timezone.utc),
        )
        self.assertEqual(r.last_call_prompt_tokens, 0)
        self.assertEqual(r.last_call_completion_tokens, 0)
        self.assertIsNone(r.context_window_limit)
        self.assertIsNone(r.context_usage_ratio)

    def test_runtime_endpoint_reads_diagnostics(self):
        """get_runtime should query agent_runs diagnostics for context data."""
        import inspect
        from session.router import get_runtime
        source = inspect.getsource(get_runtime)
        self.assertIn("last_call_prompt_tokens", source)
        self.assertIn("context_window_limit", source)
        self.assertIn("AgentRun", source)

    def test_done_event_includes_context(self):
        """SSE done event should carry context data for immediate frontend use."""
        import inspect
        from agent.executor import _finalize
        source = inspect.getsource(_finalize)
        self.assertIn('"context"', source)
        self.assertIn("context_window_limit", source)
        self.assertIn("_extract_last_call_usage", source)


if __name__ == "__main__":
    unittest.main()
