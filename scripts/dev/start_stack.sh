#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# AgentD — Start full development stack
# ═══════════════════════════════════════════════════════════════════════════════
#
# Standard startup sequence:
#   1. Check environment (.env + venv)
#   2. Run database migration (alembic upgrade head)
#   3. Start API server (with --reload)
#   4. Health check + schema verification
#   5. Start worker
#   6. Start frontend (optional)
#
# Usage:
#   scripts/dev/start_stack.sh [OPTIONS]
#
# Options:
#   --skip-migration    Skip alembic migration step
#   --skip-frontend     Don't start the frontend dev server
#   --host HOST         API host (default: 127.0.0.1)
#   --api-port PORT     API port (default: 8011)
#   --web-port PORT     Frontend port (default: 3000)
#   --worker-id ID      Worker identifier (default: worker-1)
#
# Environment:
#   AGENTD_API_HOST, AGENTD_API_PORT, AGENTD_WEB_PORT, AGENTD_WORKER_ID

source "$(dirname "$0")/../lib/common.sh"

HOST="${AGENTD_API_HOST:-$DEFAULT_API_HOST}"
API_PORT="${AGENTD_API_PORT:-$DEFAULT_API_PORT}"
WEB_PORT="${AGENTD_WEB_PORT:-$DEFAULT_WEB_PORT}"
WORKER_ID="${AGENTD_WORKER_ID:-$DEFAULT_WORKER_ID}"
SKIP_MIGRATION=false
SKIP_FRONTEND=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-migration) SKIP_MIGRATION=true; shift ;;
        --skip-frontend)  SKIP_FRONTEND=true; shift ;;
        --host)           HOST="$2"; shift 2 ;;
        --api-port)       API_PORT="$2"; shift 2 ;;
        --web-port)       WEB_PORT="$2"; shift 2 ;;
        --worker-id)      WORKER_ID="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

print_banner "Development Stack"

# ── Step 1: Pre-checks ───────────────────────────────────────────────────────
echo -e "${BOLD}[1/6] Environment check${NC}"
check_env
check_venv
ensure_dirs
echo ""

# ── Step 2: Migration ────────────────────────────────────────────────────────
echo -e "${BOLD}[2/6] Database migration${NC}"
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
echo -e "${BOLD}[3/6] API server${NC}"
existing_pid=$(read_pid "api")
if [[ -n "$existing_pid" ]] && is_running "$existing_pid"; then
    echo -e "  ${YELLOW}[api]${NC} Already running (PID=$existing_pid), skipping"
else
    echo -e "  ${CYAN}[api]${NC} Starting on $HOST:$API_PORT (dev mode, --reload)..."
    cd "$AGENTD_DIR"
    nohup $VENV_PYTHON -m uvicorn main:app --host "$HOST" --port "$API_PORT" --reload \
        >> "$LOG_DIR/api.log" 2>&1 &
    API_PID=$!
    write_pid "api" "$API_PID"
    echo -e "  ${GREEN}[api]${NC} Started (PID=$API_PID)"
fi
echo ""

# ── Step 4: Health + schema check ────────────────────────────────────────────
echo -e "${BOLD}[4/6] Health & schema check${NC}"
if ! wait_for_health "$HOST" "$API_PORT" 30; then
    echo -e "  ${RED}[health]${NC} API failed to start. Check $LOG_DIR/api.log"
    exit 1
fi
check_schema "$HOST" "$API_PORT" || true
echo ""

# ── Step 5: Start worker ─────────────────────────────────────────────────────
echo -e "${BOLD}[5/6] Worker${NC}"
existing_pid=$(read_pid "$WORKER_ID")
if [[ -n "$existing_pid" ]] && is_running "$existing_pid"; then
    echo -e "  ${YELLOW}[worker]${NC} $WORKER_ID already running (PID=$existing_pid), skipping"
else
    echo -e "  ${CYAN}[worker]${NC} Starting '$WORKER_ID'..."
    cd "$AGENTD_DIR"
    nohup $VENV_PYTHON -m agent.worker --worker-id "$WORKER_ID" \
        >> "$LOG_DIR/$WORKER_ID.log" 2>&1 &
    WORKER_PID=$!
    write_pid "$WORKER_ID" "$WORKER_PID"
    echo -e "  ${GREEN}[worker]${NC} $WORKER_ID started (PID=$WORKER_PID)"
fi
echo ""

# ── Step 6: Start frontend ───────────────────────────────────────────────────
echo -e "${BOLD}[6/6] Frontend${NC}"
if [[ "$SKIP_FRONTEND" == "true" ]]; then
    echo -e "  ${YELLOW}[web]${NC} Skipped (--skip-frontend)"
elif [[ ! -d "$WEB_DIR" ]]; then
    echo -e "  ${YELLOW}[web]${NC} web/ directory not found, skipping"
else
    existing_pid=$(read_pid "web")
    if [[ -n "$existing_pid" ]] && is_running "$existing_pid"; then
        echo -e "  ${YELLOW}[web]${NC} Already running (PID=$existing_pid), skipping"
    else
        if [[ ! -d "$WEB_DIR/node_modules" ]]; then
            echo -e "  ${YELLOW}[web]${NC} Installing npm dependencies..."
            cd "$WEB_DIR" && npm install >> "$LOG_DIR/web.log" 2>&1
        fi
        echo -e "  ${CYAN}[web]${NC} Starting on port $WEB_PORT..."
        cd "$WEB_DIR"
        PORT=$WEB_PORT npm run dev >> "$LOG_DIR/web.log" 2>&1 &
        WEB_PID=$!
        write_pid "web" "$WEB_PID"
        echo -e "  ${GREEN}[web]${NC} Started (PID=$WEB_PID)"
    fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────
print_footer
echo -e "  ${GREEN}Stack is running${NC}"
echo ""
echo "  API:      http://$HOST:$API_PORT"
echo "  Health:   http://$HOST:$API_PORT/health"
echo "  Worker:   $WORKER_ID"
if [[ "$SKIP_FRONTEND" != "true" && -d "$WEB_DIR" ]]; then
    echo "  Frontend: http://127.0.0.1:$WEB_PORT"
fi
echo "  Logs:     $LOG_DIR/"
echo "  PIDs:     $PID_DIR/"
echo ""
echo "  Stop:     scripts/dev/stop_stack.sh"
echo "  Status:   scripts/dev/status.sh"
print_footer
