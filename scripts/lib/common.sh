#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# AgentD — Shared functions for startup scripts
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ── Paths ─────────────────────────────────────────────────────────────────────
# Resolve project root from lib/ location (scripts/lib/ -> project root)
SCRIPT_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_LIB_DIR/../.." && pwd)"
AGENTD_DIR="$PROJECT_ROOT/agentd"
WEB_DIR="$PROJECT_ROOT/web"
PID_DIR="$PROJECT_ROOT/.pids"
LOG_DIR="$PROJECT_ROOT/.logs"
VENV_PYTHON="$AGENTD_DIR/.venv/bin/python"

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_API_HOST="127.0.0.1"
DEFAULT_API_PORT="8011"
DEFAULT_WEB_PORT="3000"
DEFAULT_WORKER_ID="worker-1"

# ── PID Management ────────────────────────────────────────────────────────────

ensure_dirs() {
    mkdir -p "$PID_DIR" "$LOG_DIR"
}

write_pid() {
    local name="$1" pid="$2"
    ensure_dirs
    echo "$pid" > "$PID_DIR/$name.pid"
}

read_pid() {
    local name="$1"
    local pidfile="$PID_DIR/$name.pid"
    if [[ -f "$pidfile" ]]; then
        cat "$pidfile"
    fi
}

remove_pid() {
    local name="$1"
    rm -f "$PID_DIR/$name.pid"
}

is_running() {
    local pid="$1"
    [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

stop_process() {
    local name="$1"
    local timeout="${2:-10}"
    local pid
    pid=$(read_pid "$name")

    if [[ -z "$pid" ]]; then
        echo -e "  ${YELLOW}[$name]${NC} No PID file found"
        return 0
    fi

    if ! is_running "$pid"; then
        echo -e "  ${YELLOW}[$name]${NC} Process $pid not running, cleaning PID file"
        remove_pid "$name"
        return 0
    fi

    echo -e "  ${CYAN}[$name]${NC} Stopping PID $pid (SIGTERM)..."
    kill -TERM "$pid" 2>/dev/null || true

    # Wait for graceful shutdown
    local i
    for i in $(seq 1 "$timeout"); do
        if ! is_running "$pid"; then
            echo -e "  ${GREEN}[$name]${NC} Stopped"
            remove_pid "$name"
            return 0
        fi
        sleep 1
    done

    # Force kill
    echo -e "  ${YELLOW}[$name]${NC} Still running after ${timeout}s, sending SIGKILL..."
    kill -KILL "$pid" 2>/dev/null || true
    sleep 1
    remove_pid "$name"
    echo -e "  ${GREEN}[$name]${NC} Killed"
}

# Stop all worker processes (finds worker-*.pid files)
stop_all_workers() {
    local found=false
    for pidfile in "$PID_DIR"/worker-*.pid; do
        [[ -f "$pidfile" ]] || continue
        found=true
        local name
        name=$(basename "$pidfile" .pid)
        stop_process "$name"
    done
    if [[ "$found" == "false" ]]; then
        echo -e "  ${YELLOW}[worker]${NC} No worker PID files found"
    fi
}

# ── Environment Check ─────────────────────────────────────────────────────────

check_env() {
    local env_file="$AGENTD_DIR/.env"
    if [[ ! -f "$env_file" ]]; then
        echo -e "${RED}ERROR: $env_file not found${NC}"
        echo "  Copy from template:"
        echo "    cp docs/env/local-dev.env agentd/.env"
        echo "  Then edit values to match your local setup."
        exit 1
    fi
    echo -e "  ${GREEN}[env]${NC} $env_file found"
}

check_venv() {
    if [[ ! -x "$VENV_PYTHON" ]]; then
        echo -e "${RED}ERROR: Python venv not found at $VENV_PYTHON${NC}"
        echo "  Create it:"
        echo "    cd agentd && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
        exit 1
    fi
    echo -e "  ${GREEN}[venv]${NC} Python venv OK"
}

# ── Health Check ──────────────────────────────────────────────────────────────

check_health() {
    local host="${1:-$DEFAULT_API_HOST}"
    local port="${2:-$DEFAULT_API_PORT}"
    curl -sf "http://$host:$port/health" 2>/dev/null
}

wait_for_health() {
    local host="${1:-$DEFAULT_API_HOST}"
    local port="${2:-$DEFAULT_API_PORT}"
    local max_wait="${3:-30}"

    echo -n "  [health] Waiting for API..."
    local i
    for i in $(seq 1 "$max_wait"); do
        if check_health "$host" "$port" > /dev/null 2>&1; then
            echo -e " ${GREEN}OK${NC}"
            return 0
        fi
        echo -n "."
        sleep 1
    done
    echo -e " ${RED}TIMEOUT (${max_wait}s)${NC}"
    return 1
}

# ── Schema Check ──────────────────────────────────────────────────────────────

check_schema() {
    local host="${1:-$DEFAULT_API_HOST}"
    local port="${2:-$DEFAULT_API_PORT}"
    local health
    health=$(check_health "$host" "$port")

    if [[ -z "$health" ]]; then
        echo -e "  ${RED}[schema]${NC} Cannot reach /health"
        return 1
    fi

    local schema_ok
    schema_ok=$(echo "$health" | "$VENV_PYTHON" -c "import sys,json; print(json.load(sys.stdin).get('schema_ok', False))" 2>/dev/null)

    if [[ "$schema_ok" != "True" ]]; then
        echo -e "  ${YELLOW}[schema]${NC} Schema mismatch!"
        echo "$health" | "$VENV_PYTHON" -c "
import sys, json
d = json.load(sys.stdin)
print(f'    Current: {d.get(\"schema_version\")}, Expected: {d.get(\"schema_expected\")}')
" 2>/dev/null
        echo "    Run: cd agentd && .venv/bin/python -m alembic upgrade head"
        return 1
    fi
    echo -e "  ${GREEN}[schema]${NC} OK"
}

# ── Display helpers ───────────────────────────────────────────────────────────

print_banner() {
    local title="$1"
    echo ""
    echo -e "${BOLD}═══════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}  AgentD — $title${NC}"
    echo -e "${BOLD}═══════════════════════════════════════════════════${NC}"
    echo ""
}

print_footer() {
    echo ""
    echo -e "${BOLD}═══════════════════════════════════════════════════${NC}"
    echo ""
}
