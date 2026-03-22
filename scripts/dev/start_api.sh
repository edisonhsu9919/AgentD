#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# AgentD — Start API server (development, with --reload)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Usage:
#   scripts/dev/start_api.sh [--host HOST] [--port PORT]
#
# Environment:
#   AGENTD_API_HOST  (default: 127.0.0.1)
#   AGENTD_API_PORT  (default: 8011)

source "$(dirname "$0")/../lib/common.sh"

HOST="${AGENTD_API_HOST:-$DEFAULT_API_HOST}"
PORT="${AGENTD_API_PORT:-$DEFAULT_API_PORT}"

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --host) HOST="$2"; shift 2 ;;
        --port) PORT="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# Pre-checks
check_env
check_venv

# Check if already running
existing_pid=$(read_pid "api")
if [[ -n "$existing_pid" ]] && is_running "$existing_pid"; then
    echo -e "${YELLOW}[api] Already running (PID=$existing_pid). Stop first or use stop_stack.sh${NC}"
    exit 1
fi

echo -e "  ${CYAN}[api]${NC} Starting on $HOST:$PORT (dev mode, --reload)..."
cd "$AGENTD_DIR"
$VENV_PYTHON -m uvicorn main:app --host "$HOST" --port "$PORT" --reload &
API_PID=$!
write_pid "api" "$API_PID"

wait_for_health "$HOST" "$PORT" 30
echo -e "  ${GREEN}[api]${NC} Running (PID=$API_PID) → http://$HOST:$PORT"
echo -e "  ${GREEN}[api]${NC} Health: http://$HOST:$PORT/health"
