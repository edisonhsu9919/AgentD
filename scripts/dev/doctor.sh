#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# AgentD — Diagnostic script (doctor)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Runs through a serial diagnostic checklist:
#   1. .env exists
#   2. Python venv exists
#   3. Database is reachable
#   4. Schema version matches
#   5. /health is reachable
#   6. /health.ready is true
#   7. Runtime model source
#   8. Worker process(es) alive
#
# Usage:
#   scripts/dev/doctor.sh [--host HOST] [--port PORT]

source "$(dirname "$0")/../lib/common.sh"

# ── Sanitize env for Python subprocesses ─────────────────────────────────
# doctor.sh spawns Python snippets that import core.config (pydantic-settings).
# If the parent shell has DEBUG set to a non-boolean value (e.g. "release"),
# pydantic will fail to parse it and the subprocess crashes.
# Fix: force DEBUG to a safe value so the app config loads cleanly.
# Note: uses tr for Bash 3.2 (macOS default) compatibility — no ${var,,}.
_debug_lower=$(printf '%s' "${DEBUG:-false}" | tr '[:upper:]' '[:lower:]')
case "$_debug_lower" in
    true|false|1|0|yes|no) ;;  # valid booleans for pydantic
    *) export DEBUG=false ;;
esac
unset _debug_lower

HOST="${AGENTD_API_HOST:-$DEFAULT_API_HOST}"
PORT="${AGENTD_API_PORT:-$DEFAULT_API_PORT}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --host) HOST="$2"; shift 2 ;;
        --port) PORT="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

print_banner "Doctor — Diagnostic Check"

PASS=0
FAIL=0
WARN=0

pass() { echo -e "  ${GREEN}[PASS]${NC} $1"; PASS=$((PASS + 1)); }
fail() { echo -e "  ${RED}[FAIL]${NC} $1"; FAIL=$((FAIL + 1)); }
warn() { echo -e "  ${YELLOW}[WARN]${NC} $1"; WARN=$((WARN + 1)); }

# ── 1. .env ───────────────────────────────────────────────────────────────────
echo -e "${BOLD}[1/8] Environment file${NC}"
if [[ -f "$AGENTD_DIR/.env" ]]; then
    pass ".env exists"
else
    fail ".env not found — cp docs/env/local-dev.env agentd/.env"
fi
echo ""

# ── 2. Python venv ────────────────────────────────────────────────────────────
echo -e "${BOLD}[2/8] Python virtual environment${NC}"
if [[ -x "$VENV_PYTHON" ]]; then
    py_ver=$($VENV_PYTHON --version 2>&1)
    pass "venv OK ($py_ver)"
else
    fail "venv not found at $VENV_PYTHON"
fi
echo ""

# ── 3. Database connection ────────────────────────────────────────────────────
echo -e "${BOLD}[3/8] Database connection${NC}"
db_check=$($VENV_PYTHON -c "
import asyncio, sys
async def check():
    try:
        from sqlalchemy import text
        from core.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            await db.execute(text('SELECT 1'))
        print('ok')
    except Exception as e:
        print(f'error:{e}')
import os; os.chdir('$AGENTD_DIR')
sys.path.insert(0, '$AGENTD_DIR')
asyncio.run(check())
" 2>&1)

if [[ "$db_check" == "ok" ]]; then
    pass "Database reachable"
else
    fail "Database unreachable — ${db_check#error:}"
fi
echo ""

# ── 4. Schema version ────────────────────────────────────────────────────────
echo -e "${BOLD}[4/8] Schema version${NC}"
schema_check=$($VENV_PYTHON -c "
import asyncio, sys, os
os.chdir('$AGENTD_DIR')
sys.path.insert(0, '$AGENTD_DIR')
async def check():
    try:
        from sqlalchemy import text
        from core.database import AsyncSessionLocal
        from main import EXPECTED_SCHEMA_VERSION
        async with AsyncSessionLocal() as db:
            result = await db.execute(text('SELECT version_num FROM alembic_version LIMIT 1'))
            row = result.first()
            if row is None:
                print('no_version')
                return
            current = row[0]
            if current == EXPECTED_SCHEMA_VERSION:
                print(f'ok:{current}')
            else:
                print(f'mismatch:{current}:{EXPECTED_SCHEMA_VERSION}')
    except Exception as e:
        print(f'error:{e}')
asyncio.run(check())
" 2>&1)

case "$schema_check" in
    ok:*)
        pass "Schema version ${schema_check#ok:} (up to date)"
        ;;
    mismatch:*)
        IFS=':' read -r _ current expected <<< "$schema_check"
        fail "Schema mismatch: current=$current, expected=$expected"
        echo "         Run: cd agentd && .venv/bin/python -m alembic upgrade head"
        ;;
    no_version)
        fail "No Alembic version found — run migration first"
        ;;
    *)
        fail "Schema check error: $schema_check"
        ;;
esac
echo ""

# ── 5. /health reachable ─────────────────────────────────────────────────────
echo -e "${BOLD}[5/8] API health endpoint${NC}"
health_json=$(check_health "$HOST" "$PORT" 2>/dev/null || true)

if [[ -n "$health_json" ]]; then
    pass "/health reachable at http://$HOST:$PORT/health"
else
    fail "/health unreachable — is the API running?"
    echo "         Start: scripts/dev/start_api.sh"
    # Skip remaining checks that depend on /health
    echo ""
    echo -e "${BOLD}[6/8] Readiness${NC}"
    fail "Skipped (API not running)"
    echo ""
    echo -e "${BOLD}[7/8] Runtime model${NC}"
    fail "Skipped (API not running)"
    echo ""
    echo -e "${BOLD}[8/8] Worker processes${NC}"
    # Still check workers
    worker_found=false
    for pidfile in "$PID_DIR"/worker-*.pid; do
        [[ -f "$pidfile" ]] || continue
        worker_found=true
        name=$(basename "$pidfile" .pid)
        pid=$(cat "$pidfile")
        if is_running "$pid"; then
            pass "$name running (PID=$pid)"
        else
            warn "$name stale PID=$pid"
        fi
    done
    if [[ "$worker_found" == "false" ]]; then
        warn "No worker PID files found"
    fi
    FAIL=$((FAIL + 2))  # count skipped as fails

    print_footer
    echo -e "  Results: ${GREEN}$PASS passed${NC}, ${RED}$FAIL failed${NC}, ${YELLOW}$WARN warnings${NC}"
    print_footer
    exit 1
fi
echo ""

# ── 6. Readiness ──────────────────────────────────────────────────────────────
echo -e "${BOLD}[6/8] Readiness${NC}"
ready=$("$VENV_PYTHON" -c "import sys,json; d=json.load(sys.stdin); print(d.get('ready', False))" <<< "$health_json" 2>/dev/null)
degraded=$("$VENV_PYTHON" -c "import sys,json; d=json.load(sys.stdin); r=d.get('degraded_reason'); print(r if r else 'none')" <<< "$health_json" 2>/dev/null)

if [[ "$ready" == "True" ]]; then
    pass "Service is ready"
else
    fail "Service NOT ready — degraded_reason=$degraded"
fi
echo ""

# ── 7. Runtime model ──────────────────────────────────────────────────────────
echo -e "${BOLD}[7/8] Runtime model${NC}"
model_info=$("$VENV_PYTHON" -c "
import sys, json
d = json.load(sys.stdin)
src = d.get('runtime_model_source', '?')
m = d.get('runtime_model') or {}
name = m.get('name', '?')
mid = m.get('model_id', '?')
url = m.get('base_url_masked', '?')
print(f'{src}|{name}|{mid}|{url}')
" <<< "$health_json" 2>/dev/null)

IFS='|' read -r src name mid url <<< "$model_info"
if [[ "$src" == "db_default" ]]; then
    pass "Model from DB: $name ($mid)"
elif [[ "$src" == "env_fallback" ]]; then
    warn "Model from env fallback: $name ($mid)"
    echo "         Consider setting a DB default via admin model config API"
else
    fail "Model source unknown: $src"
fi
echo "         base_url: $url"
echo ""

# ── 8. Worker processes ───────────────────────────────────────────────────────
echo -e "${BOLD}[8/8] Worker processes${NC}"
worker_found=false
worker_running=0
for pidfile in "$PID_DIR"/worker-*.pid; do
    [[ -f "$pidfile" ]] || continue
    worker_found=true
    name=$(basename "$pidfile" .pid)
    pid=$(cat "$pidfile")
    if is_running "$pid"; then
        pass "$name running (PID=$pid)"
        worker_running=$((worker_running + 1))
    else
        warn "$name stale PID=$pid — restart with scripts/dev/start_worker.sh"
    fi
done
if [[ "$worker_found" == "false" ]]; then
    warn "No worker PID files found — start with scripts/dev/start_worker.sh"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
print_footer
echo -e "  Results: ${GREEN}$PASS passed${NC}, ${RED}$FAIL failed${NC}, ${YELLOW}$WARN warnings${NC}"

if [[ $FAIL -gt 0 ]]; then
    echo -e "  ${RED}Some checks failed — fix issues above before proceeding${NC}"
elif [[ $WARN -gt 0 ]]; then
    echo -e "  ${YELLOW}All critical checks passed, but some warnings need attention${NC}"
else
    echo -e "  ${GREEN}All checks passed — system is healthy${NC}"
fi
print_footer

[[ $FAIL -eq 0 ]]
