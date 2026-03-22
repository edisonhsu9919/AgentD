"""Tests for Phase I3 — Startup Orchestration.

Covers:
  - Script existence and executability
  - Script directory structure
  - Health endpoint instance tracking fields
  - common.sh structure
  - PID management logic (unit-level)
"""

import os
import stat
from pathlib import Path

import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# I3: Script directory structure
# ═══════════════════════════════════════════════════════════════════════════════

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # agentd/tests -> agentd -> project root
SCRIPTS_DIR = PROJECT_ROOT / "scripts"


class TestScriptDirectoryStructure:
    """Verify scripts/ directory layout matches I3 contract."""

    def test_scripts_dir_exists(self):
        assert SCRIPTS_DIR.is_dir(), "scripts/ directory must exist at project root"

    def test_lib_dir_exists(self):
        assert (SCRIPTS_DIR / "lib").is_dir()

    def test_dev_dir_exists(self):
        assert (SCRIPTS_DIR / "dev").is_dir()

    def test_server_dir_exists(self):
        assert (SCRIPTS_DIR / "server").is_dir()

    def test_common_sh_exists(self):
        assert (SCRIPTS_DIR / "lib" / "common.sh").is_file()


# ═══════════════════════════════════════════════════════════════════════════════
# I3: Dev scripts existence and executability
# ═══════════════════════════════════════════════════════════════════════════════


DEV_SCRIPTS = [
    "start_api.sh",
    "start_worker.sh",
    "start_frontend.sh",
    "start_stack.sh",
    "stop_stack.sh",
    "status.sh",
]


class TestDevScripts:
    """Verify all dev scripts exist and are executable."""

    @pytest.mark.parametrize("script", DEV_SCRIPTS)
    def test_script_exists(self, script):
        path = SCRIPTS_DIR / "dev" / script
        assert path.is_file(), f"scripts/dev/{script} must exist"

    @pytest.mark.parametrize("script", DEV_SCRIPTS)
    def test_script_executable(self, script):
        path = SCRIPTS_DIR / "dev" / script
        mode = path.stat().st_mode
        assert mode & stat.S_IXUSR, f"scripts/dev/{script} must be user-executable"

    @pytest.mark.parametrize("script", DEV_SCRIPTS)
    def test_script_sources_common(self, script):
        """Each dev script must source lib/common.sh."""
        content = (SCRIPTS_DIR / "dev" / script).read_text()
        assert "common.sh" in content, f"{script} must source common.sh"


# ═══════════════════════════════════════════════════════════════════════════════
# I3: Server scripts existence and executability
# ═══════════════════════════════════════════════════════════════════════════════


SERVER_SCRIPTS = [
    "start_api.sh",
    "start_worker.sh",
    "start_stack.sh",
    "stop_stack.sh",
    "status.sh",
]


class TestServerScripts:
    """Verify all server scripts exist and are executable."""

    @pytest.mark.parametrize("script", SERVER_SCRIPTS)
    def test_script_exists(self, script):
        path = SCRIPTS_DIR / "server" / script
        assert path.is_file(), f"scripts/server/{script} must exist"

    @pytest.mark.parametrize("script", SERVER_SCRIPTS)
    def test_script_executable(self, script):
        path = SCRIPTS_DIR / "server" / script
        mode = path.stat().st_mode
        assert mode & stat.S_IXUSR, f"scripts/server/{script} must be user-executable"

    @pytest.mark.parametrize("script", SERVER_SCRIPTS)
    def test_script_sources_common(self, script):
        content = (SCRIPTS_DIR / "server" / script).read_text()
        assert "common.sh" in content

    def test_server_api_no_reload(self):
        """Server API script must NOT use --reload in uvicorn command."""
        content = (SCRIPTS_DIR / "server" / "start_api.sh").read_text()
        # Extract only non-comment lines (actual commands)
        cmd_lines = [l for l in content.splitlines() if l.strip() and not l.strip().startswith("#")]
        cmd_text = "\n".join(cmd_lines)
        assert "--reload" not in cmd_text, "Server API must not use --reload in commands"

    def test_dev_api_has_reload(self):
        """Dev API script MUST use --reload in uvicorn command."""
        content = (SCRIPTS_DIR / "dev" / "start_api.sh").read_text()
        cmd_lines = [l for l in content.splitlines() if l.strip() and not l.strip().startswith("#")]
        cmd_text = "\n".join(cmd_lines)
        assert "--reload" in cmd_text, "Dev API must use --reload"


# ═══════════════════════════════════════════════════════════════════════════════
# I3: common.sh structure
# ═══════════════════════════════════════════════════════════════════════════════


class TestCommonSh:
    """Verify common.sh contains required shared functions."""

    @pytest.fixture
    def common_content(self):
        return (SCRIPTS_DIR / "lib" / "common.sh").read_text()

    def test_has_pid_management(self, common_content):
        assert "write_pid" in common_content
        assert "read_pid" in common_content
        assert "stop_process" in common_content

    def test_has_health_check(self, common_content):
        assert "check_health" in common_content
        assert "wait_for_health" in common_content

    def test_has_env_check(self, common_content):
        assert "check_env" in common_content
        assert "check_venv" in common_content

    def test_has_schema_check(self, common_content):
        assert "check_schema" in common_content

    def test_has_stop_all_workers(self, common_content):
        assert "stop_all_workers" in common_content

    def test_uses_sigterm_then_sigkill(self, common_content):
        """Stop logic must use TERM then KILL pattern."""
        assert "SIGTERM" in common_content or "-TERM" in common_content
        assert "SIGKILL" in common_content or "-KILL" in common_content

    def test_default_ports(self, common_content):
        """Verify default port constants match contract."""
        assert '"8011"' in common_content  # API port
        assert '"3000"' in common_content  # Frontend port


# ═══════════════════════════════════════════════════════════════════════════════
# I3: Health endpoint instance tracking
# ═══════════════════════════════════════════════════════════════════════════════


class TestHealthEndpointFields:
    """Verify health endpoint includes instance tracking fields."""

    def test_instance_id_exists_in_main(self):
        """main.py must define _INSTANCE_ID."""
        import main
        assert hasattr(main, "_INSTANCE_ID")
        assert isinstance(main._INSTANCE_ID, str)
        assert len(main._INSTANCE_ID) == 12

    def test_started_at_exists_in_main(self):
        """main.py must define _STARTED_AT."""
        import main
        assert hasattr(main, "_STARTED_AT")

    def test_health_endpoint_registered(self):
        from main import app
        paths = [route.path for route in app.routes]
        assert "/health" in paths

    def test_health_returns_instance_fields(self):
        """Health response schema must include instance tracking fields."""
        import inspect
        import main
        source = inspect.getsource(main.health)
        assert "instance_id" in source
        assert "started_at" in source
        assert "pid" in source


# ═══════════════════════════════════════════════════════════════════════════════
# I3: Start sequence
# ═══════════════════════════════════════════════════════════════════════════════


class TestStartSequence:
    """Verify start_stack scripts follow the correct startup order."""

    def _strip_comments(self, content: str) -> str:
        """Return only non-comment lines for order checking."""
        return "\n".join(
            l for l in content.splitlines()
            if l.strip() and not l.strip().startswith("#")
        )

    def test_dev_stack_order(self):
        """Dev stack: env → migration → API → health → worker → frontend."""
        content = self._strip_comments((SCRIPTS_DIR / "dev" / "start_stack.sh").read_text())
        env_pos = content.find("check_env")
        migration_pos = content.find("alembic upgrade head")
        api_pos = content.find("uvicorn main:app")
        health_pos = content.find("wait_for_health")
        worker_pos = content.find("agent.worker")
        frontend_pos = content.find("npm run dev")

        assert env_pos < migration_pos < api_pos < health_pos < worker_pos < frontend_pos, \
            "Start sequence must be: env → migration → API → health → worker → frontend"

    def test_server_stack_order(self):
        """Server stack: env → migration → API → health → worker."""
        content = self._strip_comments((SCRIPTS_DIR / "server" / "start_stack.sh").read_text())
        env_pos = content.find("check_env")
        migration_pos = content.find("alembic upgrade head")
        api_pos = content.find("uvicorn main:app")
        health_pos = content.find("wait_for_health")
        worker_pos = content.find("agent.worker")

        assert env_pos < migration_pos < api_pos < health_pos < worker_pos, \
            "Start sequence must be: env → migration → API → health → worker"


class TestStopSequence:
    """Verify stop_stack scripts follow correct shutdown order."""

    def test_dev_stop_order(self):
        """Dev stop: frontend → workers → API (reverse of start)."""
        content = (SCRIPTS_DIR / "dev" / "stop_stack.sh").read_text()
        web_pos = content.find('"web"')
        workers_pos = content.find("stop_all_workers")
        api_pos = content.find('"api"')
        # web before workers before api
        assert web_pos < workers_pos < api_pos

    def test_server_stop_order(self):
        """Server stop: workers → API."""
        content = (SCRIPTS_DIR / "server" / "stop_stack.sh").read_text()
        workers_pos = content.find("stop_all_workers")
        api_pos = content.find('"api"')
        assert workers_pos < api_pos


# ═══════════════════════════════════════════════════════════════════════════════
# I3: .gitignore
# ═══════════════════════════════════════════════════════════════════════════════


class TestGitignore:
    """Verify .pids/ and .logs/ are gitignored."""

    def test_gitignore_exists(self):
        assert (PROJECT_ROOT / ".gitignore").is_file()

    def test_pids_ignored(self):
        content = (PROJECT_ROOT / ".gitignore").read_text()
        assert ".pids/" in content

    def test_logs_ignored(self):
        content = (PROJECT_ROOT / ".gitignore").read_text()
        assert ".logs/" in content


# ═══════════════════════════════════════════════════════════════════════════════
# I3: Audit fix verification
# ═══════════════════════════════════════════════════════════════════════════════


class TestAuditFixes:
    """Verify fixes for issues found during I3 live audit."""

    def _strip_comments(self, content: str) -> str:
        return "\n".join(
            l for l in content.splitlines()
            if l.strip() and not l.strip().startswith("#")
        )

    def test_dev_stack_uses_nohup_for_api(self):
        """Fix #1: dev start_stack.sh must use nohup for PID stability."""
        content = self._strip_comments(
            (SCRIPTS_DIR / "dev" / "start_stack.sh").read_text()
        )
        # Find the uvicorn line and check it has nohup
        for line in content.splitlines():
            if "uvicorn main:app" in line:
                assert "nohup" in line, "dev start_stack API must use nohup"
                break

    def test_dev_stack_ensures_dirs_early(self):
        """Fix #2: start_stack.sh must call ensure_dirs before any log redirect."""
        content = self._strip_comments(
            (SCRIPTS_DIR / "dev" / "start_stack.sh").read_text()
        )
        ensure_pos = content.find("ensure_dirs")
        log_pos = content.find("LOG_DIR")
        assert ensure_pos != -1, "ensure_dirs must be called"
        assert ensure_pos < log_pos, "ensure_dirs must come before first LOG_DIR usage"

    def test_server_stack_ensures_dirs_early(self):
        """Fix #2 (server): server start_stack.sh must call ensure_dirs before log redirect."""
        content = self._strip_comments(
            (SCRIPTS_DIR / "server" / "start_stack.sh").read_text()
        )
        ensure_pos = content.find("ensure_dirs")
        log_pos = content.find("LOG_DIR")
        assert ensure_pos != -1, "ensure_dirs must be called"
        assert ensure_pos < log_pos, "ensure_dirs must come before first LOG_DIR usage"

    def test_server_api_health_check_uses_loopback(self):
        """Fix #3: server/start_api.sh must health-check via 127.0.0.1, not $HOST."""
        content = self._strip_comments(
            (SCRIPTS_DIR / "server" / "start_api.sh").read_text()
        )
        # Find wait_for_health call
        for line in content.splitlines():
            if "wait_for_health" in line:
                assert "127.0.0.1" in line, \
                    "server start_api health check must use 127.0.0.1, not $HOST"
                assert "$HOST" not in line, \
                    "server start_api health check must not use $HOST"
                break

    def test_no_worker_double_prefix(self):
        """Fix #4: worker PID files must not have worker-worker-* double prefix."""
        for script_dir in ["dev", "server"]:
            for script_name in ["start_worker.sh", "start_stack.sh"]:
                path = SCRIPTS_DIR / script_dir / script_name
                if not path.exists():
                    continue
                content = self._strip_comments(path.read_text())
                assert '"worker-$WORKER_ID"' not in content, \
                    f"{script_dir}/{script_name} must not use worker-$WORKER_ID for PID naming"
                assert '"worker-$wid"' not in content, \
                    f"{script_dir}/{script_name} must not use worker-$wid for PID naming"
