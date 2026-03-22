#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# AgentD — Start frontend dev server
# ═══════════════════════════════════════════════════════════════════════════════
#
# Usage:
#   scripts/dev/start_frontend.sh [--port PORT]
#
# Environment:
#   AGENTD_WEB_PORT  (default: 3000)

source "$(dirname "$0")/../lib/common.sh"

PORT="${AGENTD_WEB_PORT:-$DEFAULT_WEB_PORT}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port) PORT="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [[ ! -d "$WEB_DIR" ]]; then
    echo -e "${RED}ERROR: web/ directory not found at $WEB_DIR${NC}"
    exit 1
fi

# Check if already running
existing_pid=$(read_pid "web")
if [[ -n "$existing_pid" ]] && is_running "$existing_pid"; then
    echo -e "${YELLOW}[web] Already running (PID=$existing_pid)${NC}"
    exit 1
fi

# Install deps if needed
if [[ ! -d "$WEB_DIR/node_modules" ]]; then
    echo -e "  ${YELLOW}[web]${NC} node_modules not found, running npm install..."
    cd "$WEB_DIR" && npm install
fi

echo -e "  ${CYAN}[web]${NC} Starting frontend on port $PORT..."
cd "$WEB_DIR"
PORT=$PORT npm run dev &
WEB_PID=$!
write_pid "web" "$WEB_PID"

echo -e "  ${GREEN}[web]${NC} Running (PID=$WEB_PID) → http://127.0.0.1:$PORT"
