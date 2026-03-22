#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# AgentD — Start API server (production / server deployment)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Runs uvicorn WITHOUT --reload, with configurable workers.
#
# Usage:
#   scripts/server/start_api.sh [--host HOST] [--port PORT] [--workers N]
#
# Environment:
#   AGENTD_API_HOST    (default: 0.0.0.0)
#   AGENTD_API_PORT    (default: 8011)
#   AGENTD_API_WORKERS (default: 1)

source "$(dirname "$0")/../lib/common.sh"

HOST="${AGENTD_API_HOST:-0.0.0.0}"
PORT="${AGENTD_API_PORT:-$DEFAULT_API_PORT}"
WORKERS="${AGENTD_API_WORKERS:-1}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --host)    HOST="$2"; shift 2 ;;
        --port)    PORT="$2"; shift 2 ;;
        --workers) WORKERS="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# Pre-checks
check_env
check_venv

# Check if already running
existing_pid=$(read_pid "api")
if [[ -n "$existing_pid" ]] && is_running "$existing_pid"; then
    echo -e "${YELLOW}[api] Already running (PID=$existing_pid). Stop first.${NC}"
    exit 1
fi

ensure_dirs

echo -e "  ${CYAN}[api]${NC} Starting on $HOST:$PORT (workers=$WORKERS)..."
cd "$AGENTD_DIR"
nohup $VENV_PYTHON -m uvicorn main:app \
    --host "$HOST" \
    --port "$PORT" \
    --workers "$WORKERS" \
    >> "$LOG_DIR/api.log" 2>&1 &
API_PID=$!
write_pid "api" "$API_PID"

# Health check always via loopback, even when bound to 0.0.0.0
wait_for_health "127.0.0.1" "$PORT" 30
echo -e "  ${GREEN}[api]${NC} Running (PID=$API_PID, workers=$WORKERS)"
