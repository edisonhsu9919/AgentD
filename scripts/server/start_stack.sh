#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# AgentD — Start server stack (migration + API + worker)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Standard startup sequence:
#   1. Check environment (.env + venv)
#   2. Run database migration
#   3. Start API server (no --reload, configurable workers)
#   4. Health check + schema verification
#   5. Start worker(s)
#
# Usage:
#   scripts/server/start_stack.sh [OPTIONS]
#
# Options:
#   --skip-migration     Skip alembic migration step
#   --host HOST          API bind host (default: 0.0.0.0)
#   --api-port PORT      API port (default: 8011)
#   --api-workers N      Uvicorn workers (default: 1)
#   --worker-id ID       Worker identifier (default: worker-1)
#   --num-workers N      Number of agent workers to start (default: 1)

source "$(dirname "$0")/../lib/common.sh"

HOST="${AGENTD_API_HOST:-0.0.0.0}"
API_PORT="${AGENTD_API_PORT:-$DEFAULT_API_PORT}"
API_WORKERS="${AGENTD_API_WORKERS:-1}"
WORKER_ID="${AGENTD_WORKER_ID:-$DEFAULT_WORKER_ID}"
NUM_WORKERS="${AGENTD_NUM_WORKERS:-1}"
SKIP_MIGRATION=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-migration) SKIP_MIGRATION=true; shift ;;
        --host)           HOST="$2"; shift 2 ;;
        --api-port)       API_PORT="$2"; shift 2 ;;
        --api-workers)    API_WORKERS="$2"; shift 2 ;;
        --worker-id)      WORKER_ID="$2"; shift 2 ;;
        --num-workers)    NUM_WORKERS="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

print_banner "Server Stack"

# ── Step 1: Pre-checks ───────────────────────────────────────────────────────
echo -e "${BOLD}[1/5] Environment check${NC}"
check_env
check_venv
ensure_dirs
echo ""

# ── Step 2: Migration ────────────────────────────────────────────────────────
echo -e "${BOLD}[2/5] Database migration${NC}"
if [[ "$SKIP_MIGRATION" == "true" ]]; then
    echo -e "  ${YELLOW}[migration]${NC} Skipped (--skip-migration)"
else
    echo -e "  ${CYAN}[migration]${NC} Running alembic upgrade head..."
    cd "$AGENTD_DIR"
    $VENV_PYTHON -m alembic upgrade head
    echo -e "  ${GREEN}[migration]${NC} Done"
fi
echo ""

# ── Step 3: Start API ────────────────────────────────────────────────────────
echo -e "${BOLD}[3/5] API server${NC}"
existing_pid=$(read_pid "api")
if [[ -n "$existing_pid" ]] && is_running "$existing_pid"; then
    echo -e "  ${YELLOW}[api]${NC} Already running (PID=$existing_pid), skipping"
else
    echo -e "  ${CYAN}[api]${NC} Starting on $HOST:$API_PORT (workers=$API_WORKERS)..."
    cd "$AGENTD_DIR"
    nohup $VENV_PYTHON -m uvicorn main:app \
        --host "$HOST" \
        --port "$API_PORT" \
        --workers "$API_WORKERS" \
        >> "$LOG_DIR/api.log" 2>&1 &
    API_PID=$!
    write_pid "api" "$API_PID"
    echo -e "  ${GREEN}[api]${NC} Started (PID=$API_PID)"
fi
echo ""

# ── Step 4: Health + schema check ────────────────────────────────────────────
echo -e "${BOLD}[4/5] Health & schema check${NC}"
# For health check, use 127.0.0.1 even if bound to 0.0.0.0
HEALTH_HOST="127.0.0.1"
if ! wait_for_health "$HEALTH_HOST" "$API_PORT" 30; then
    echo -e "  ${RED}[health]${NC} API failed to start. Check $LOG_DIR/api.log"
    exit 1
fi
check_schema "$HEALTH_HOST" "$API_PORT" || true
echo ""

# ── Step 5: Start worker(s) ──────────────────────────────────────────────────
echo -e "${BOLD}[5/5] Worker(s)${NC}"
for i in $(seq 1 "$NUM_WORKERS"); do
    if [[ "$NUM_WORKERS" -eq 1 ]]; then
        wid="$WORKER_ID"
    else
        wid="worker-$i"
    fi

    existing_pid=$(read_pid "$wid")
    if [[ -n "$existing_pid" ]] && is_running "$existing_pid"; then
        echo -e "  ${YELLOW}[worker]${NC} $wid already running (PID=$existing_pid), skipping"
    else
        cd "$AGENTD_DIR"
        nohup $VENV_PYTHON -m agent.worker --worker-id "$wid" \
            >> "$LOG_DIR/$wid.log" 2>&1 &
        WORKER_PID=$!
        write_pid "$wid" "$WORKER_PID"
        echo -e "  ${GREEN}[worker]${NC} $wid started (PID=$WORKER_PID)"
    fi
done

# ── Summary ───────────────────────────────────────────────────────────────────
print_footer
echo -e "  ${GREEN}Server stack is running${NC}"
echo ""
echo "  API:      http://$HOST:$API_PORT (workers=$API_WORKERS)"
echo "  Health:   http://127.0.0.1:$API_PORT/health"
echo "  Workers:  $NUM_WORKERS"
echo "  Logs:     $LOG_DIR/"
echo "  PIDs:     $PID_DIR/"
echo ""
echo "  Stop:     scripts/server/stop_stack.sh"
echo "  Status:   scripts/server/status.sh"
print_footer
