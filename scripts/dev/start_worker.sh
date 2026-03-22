#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# AgentD — Start a worker process (development)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Usage:
#   scripts/dev/start_worker.sh [WORKER_ID]
#
# Examples:
#   scripts/dev/start_worker.sh                  # worker-1
#   scripts/dev/start_worker.sh worker-dev-2     # custom ID

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

echo -e "  ${CYAN}[worker]${NC} Starting '$WORKER_ID'..."
cd "$AGENTD_DIR"
$VENV_PYTHON -m agent.worker --worker-id "$WORKER_ID" &
WORKER_PID=$!
write_pid "$WORKER_ID" "$WORKER_PID"

echo -e "  ${GREEN}[worker]${NC} $WORKER_ID running (PID=$WORKER_PID)"
