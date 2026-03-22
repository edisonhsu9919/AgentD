#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# AgentD — Show status of all stack components
# ═══════════════════════════════════════════════════════════════════════════════
#
# Checks:
#   - API process + /health endpoint
#   - Worker process(es)
#   - Frontend process
#
# Usage:
#   scripts/dev/status.sh

source "$(dirname "$0")/../lib/common.sh"

HOST="${AGENTD_API_HOST:-$DEFAULT_API_HOST}"
API_PORT="${AGENTD_API_PORT:-$DEFAULT_API_PORT}"

print_banner "Status"

# ── API ───────────────────────────────────────────────────────────────────────
api_pid=$(read_pid "api")
if [[ -n "$api_pid" ]] && is_running "$api_pid"; then
    echo -e "  API:        ${GREEN}running${NC} (PID=$api_pid)"

    # Health check
    health=$(check_health "$HOST" "$API_PORT" 2>/dev/null || true)
    if [[ -n "$health" ]]; then
        "$VENV_PYTHON" -c "
import sys, json
d = json.load(sys.stdin)
status = d.get('status', '?')
version = d.get('version', '?')
schema = d.get('schema_version', '?')
schema_ok = 'ok' if d.get('schema_ok') else 'MISMATCH'
instance = d.get('instance_id', '?')
pid = d.get('pid', '?')
started = d.get('started_at', '?')
print(f'  Health:     status={status} version={version} schema={schema}({schema_ok})')
if instance != '?':
    print(f'  Instance:   {instance} (PID={pid}, started={started})')
" <<< "$health" 2>/dev/null || true
    else
        echo -e "  Health:     ${RED}unreachable${NC} (http://$HOST:$API_PORT/health)"
    fi
else
    echo -e "  API:        ${RED}stopped${NC}"
fi

echo ""

# ── Workers ───────────────────────────────────────────────────────────────────
worker_count=0
worker_found=false
for pidfile in "$PID_DIR"/worker-*.pid; do
    [[ -f "$pidfile" ]] || continue
    worker_found=true
    name=$(basename "$pidfile" .pid)
    pid=$(cat "$pidfile")
    if is_running "$pid"; then
        echo -e "  Worker:     ${GREEN}running${NC} ($name, PID=$pid)"
        ((worker_count++))
    else
        echo -e "  Worker:     ${RED}stopped${NC} ($name, stale PID=$pid)"
    fi
done
if [[ "$worker_found" == "false" ]]; then
    echo -e "  Worker:     ${YELLOW}none${NC}"
fi

echo ""

# ── Frontend ──────────────────────────────────────────────────────────────────
web_pid=$(read_pid "web")
if [[ -n "$web_pid" ]] && is_running "$web_pid"; then
    echo -e "  Frontend:   ${GREEN}running${NC} (PID=$web_pid)"
else
    echo -e "  Frontend:   ${YELLOW}not started${NC}"
fi

print_footer
