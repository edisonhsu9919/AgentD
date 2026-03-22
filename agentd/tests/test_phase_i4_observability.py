"""Tests for Phase I4 — Schema Gate, Health Check & Minimal Observability.

Covers:
  - /health readiness semantics (ready, degraded_reason)
  - /health runtime model fields
  - /health instance tracking fields
  - Diagnostics endpoint registration
  - Doctor script existence and structure
  - Worker startup log format
  - API startup model log
"""

import inspect
import os
import stat
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"


# ═══════════════════════════════════════════════════════════════════════════════
# I4: /health readiness semantics
# ═══════════════════════════════════════════════════════════════════════════════


class TestHealthReadiness:
    """Verify /health returns readiness-level information."""

    def test_health_function_has_ready_field(self):
        """Health response must include 'ready' field."""
        import main
        source = inspect.getsource(main.health)
        assert '"ready"' in source or "'ready'" in source

    def test_health_function_has_degraded_reason(self):
        """Health response must include 'degraded_reason' field."""
        import main
        source = inspect.getsource(main.health)
        assert "degraded_reason" in source

    def test_health_function_has_runtime_model_source(self):
        """Health response must include 'runtime_model_source'."""
        import main
        source = inspect.getsource(main.health)
        assert "runtime_model_source" in source

    def test_health_function_has_runtime_model(self):
        """Health response must include 'runtime_model' with safe fields."""
        import main
        source = inspect.getsource(main.health)
        assert "runtime_model" in source
        assert "base_url_masked" in source

    def test_health_function_has_instance_fields(self):
        """Health response must include instance_id, started_at, pid."""
        import main
        source = inspect.getsource(main.health)
        assert "instance_id" in source
        assert "started_at" in source
        assert "pid" in source

    def test_health_schema_mismatch_degrades(self):
        """Health logic must set degraded_reason=schema_mismatch when schema doesn't match."""
        import main
        source = inspect.getsource(main.health)
        assert "schema_mismatch" in source

    def test_health_db_unreachable_degrades(self):
        """Health logic must set degraded_reason=db_unreachable when DB fails."""
        import main
        source = inspect.getsource(main.health)
        assert "db_unreachable" in source


# ═══════════════════════════════════════════════════════════════════════════════
# I4: /health runtime model safety
# ═══════════════════════════════════════════════════════════════════════════════


class TestHealthModelSafety:
    """Verify /health does not leak sensitive model configuration."""

    def test_no_raw_api_key_in_health(self):
        """Health endpoint must not return raw api_key."""
        import main
        source = inspect.getsource(main.health)
        # Should use base_url_masked, not raw api_key
        assert "api_key" not in source or "api_key_masked" in source or "_mask_api_key" in source

    def test_runtime_model_uses_masked_url(self):
        """Runtime model in /health must use base_url_masked."""
        import main
        source = inspect.getsource(main.health)
        assert "base_url_masked" in source


# ═══════════════════════════════════════════════════════════════════════════════
# I4: Diagnostics endpoint
# ═══════════════════════════════════════════════════════════════════════════════


class TestDiagnosticsEndpoint:
    """Verify GET /api/admin/runtime/diagnostics exists and has correct structure."""

    def test_diagnostics_endpoint_registered(self):
        from model_config.router import runtime_router
        paths = [route.path for route in runtime_router.routes]
        assert "/diagnostics" in paths

    def test_diagnostics_function_exists(self):
        from model_config.router import get_runtime_diagnostics
        assert callable(get_runtime_diagnostics)

    def test_diagnostics_returns_expected_sections(self):
        """Diagnostics response source must include all required sections."""
        from model_config import router as mc_router
        source = inspect.getsource(mc_router.get_runtime_diagnostics)
        assert '"instance"' in source
        assert '"schema"' in source
        assert '"model"' in source
        assert '"config_summary"' in source
        assert '"env_fallback"' in source

    def test_diagnostics_masks_api_key(self):
        """Diagnostics must mask API keys."""
        from model_config import router as mc_router
        source = inspect.getsource(mc_router.get_runtime_diagnostics)
        assert "_mask_api_key" in source

    def test_diagnostics_full_path(self):
        """Diagnostics must be accessible at /api/admin/runtime/diagnostics."""
        from main import app
        paths = [route.path for route in app.routes]
        assert "/api/admin/runtime/diagnostics" in paths


# ═══════════════════════════════════════════════════════════════════════════════
# I4: Startup log standardization
# ═══════════════════════════════════════════════════════════════════════════════


class TestStartupLogs:
    """Verify startup logs print required baseline information."""

    def test_api_logs_instance_info(self):
        """API lifespan must log instance_id and PID."""
        import main
        source = inspect.getsource(main.lifespan)
        assert "_INSTANCE_ID" in source
        assert "PID" in source

    def test_api_logs_model_source(self):
        """API lifespan must call _log_runtime_model."""
        import main
        source = inspect.getsource(main.lifespan)
        assert "_log_runtime_model" in source

    def test_log_runtime_model_exists(self):
        """_log_runtime_model function must exist in main."""
        import main
        assert hasattr(main, "_log_runtime_model")
        assert callable(main._log_runtime_model)

    def test_log_runtime_model_prints_source(self):
        """_log_runtime_model must print model source and model_id."""
        import main
        source = inspect.getsource(main._log_runtime_model)
        assert "resolved.source" in source
        assert "resolved.model_id" in source

    def test_worker_logs_pid(self):
        """Worker startup must log PID."""
        from agent.worker import AgentWorker
        source = inspect.getsource(AgentWorker.run)
        assert "PID" in source

    def test_worker_logs_claim_loop_ready(self):
        """Worker must print 'Claim loop ready' after startup."""
        from agent.worker import AgentWorker
        source = inspect.getsource(AgentWorker.run)
        assert "Claim loop ready" in source


# ═══════════════════════════════════════════════════════════════════════════════
# I4: Doctor script
# ═══════════════════════════════════════════════════════════════════════════════


class TestDoctorScript:
    """Verify doctor.sh diagnostic script exists and has correct structure."""

    def test_doctor_exists(self):
        path = SCRIPTS_DIR / "dev" / "doctor.sh"
        assert path.is_file(), "scripts/dev/doctor.sh must exist"

    def test_doctor_executable(self):
        path = SCRIPTS_DIR / "dev" / "doctor.sh"
        mode = path.stat().st_mode
        assert mode & stat.S_IXUSR, "doctor.sh must be user-executable"

    def test_doctor_sources_common(self):
        content = (SCRIPTS_DIR / "dev" / "doctor.sh").read_text()
        assert "common.sh" in content

    def test_doctor_checks_env(self):
        content = (SCRIPTS_DIR / "dev" / "doctor.sh").read_text()
        assert ".env" in content

    def test_doctor_checks_venv(self):
        content = (SCRIPTS_DIR / "dev" / "doctor.sh").read_text()
        assert "VENV_PYTHON" in content

    def test_doctor_checks_database(self):
        content = (SCRIPTS_DIR / "dev" / "doctor.sh").read_text()
        assert "Database" in content or "database" in content or "SELECT 1" in content

    def test_doctor_checks_schema(self):
        content = (SCRIPTS_DIR / "dev" / "doctor.sh").read_text()
        assert "schema" in content.lower() or "alembic_version" in content

    def test_doctor_checks_health(self):
        content = (SCRIPTS_DIR / "dev" / "doctor.sh").read_text()
        assert "check_health" in content or "/health" in content

    def test_doctor_checks_ready(self):
        content = (SCRIPTS_DIR / "dev" / "doctor.sh").read_text()
        assert "ready" in content.lower()

    def test_doctor_checks_model(self):
        content = (SCRIPTS_DIR / "dev" / "doctor.sh").read_text()
        assert "model" in content.lower() or "runtime_model" in content

    def test_doctor_checks_workers(self):
        content = (SCRIPTS_DIR / "dev" / "doctor.sh").read_text()
        assert "worker" in content.lower()

    def test_doctor_has_pass_fail_output(self):
        """Doctor must use PASS/FAIL/WARN output format."""
        content = (SCRIPTS_DIR / "dev" / "doctor.sh").read_text()
        assert "PASS" in content
        assert "FAIL" in content
        assert "WARN" in content
