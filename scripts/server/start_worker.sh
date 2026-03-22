#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# AgentD — Start a worker process (server deployment)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Usage:
#   scripts/server/start_worker.sh [WORKER_ID]
#
# Defaults:
#   - 1 worker recommended for baseline
#   - 2 workers recommended for concurrent workloads
#   - Higher counts: evaluate based on LLM throughput and DB pool

source "$(dirname "$0")/../lib/common.sh"

WORKER_ID="${1:-$DEFAULT_WORKER_ID}"

# Pre-checks
check_env
check_venv

# Check if already running
existing_pid=$(read_pid "$WORKER_ID")
if [[ -n "$existing_pid" ]] && is_running "$existing_pid"; then
    echo -e "${YELLOW}[worker] $WORKER_ID already running (PID=$existing_pid)${NC}"
    exit 1
fi

ensure_dirs

echo -e "  ${CYAN}[worker]${NC} Starting '$WORKER_ID'..."
cd "$AGENTD_DIR"
nohup $VENV_PYTHON -m agent.worker --worker-id "$WORKER_ID" \
    >> "$LOG_DIR/$WORKER_ID.log" 2>&1 &
WORKER_PID=$!
write_pid "$WORKER_ID" "$WORKER_PID"

echo -e "  ${GREEN}[worker]${NC} $WORKER_ID running (PID=$WORKER_PID)"
echo -e "  ${GREEN}[worker]${NC} Log: $LOG_DIR/$WORKER_ID.log"
