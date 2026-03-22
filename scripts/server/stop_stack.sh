#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# AgentD — Stop server stack
# ═══════════════════════════════════════════════════════════════════════════════
#
# Stop order (reverse of start):
#   1. Workers (all) — prevent new task claims
#   2. API — allow in-flight requests to drain
#
# Each process: SIGTERM → wait 10s → SIGKILL
#
# Usage:
#   scripts/server/stop_stack.sh

source "$(dirname "$0")/../lib/common.sh"

print_banner "Stopping Server Stack"

# Step 1: Stop all workers first (prevent new task claims)
echo -e "${BOLD}[1/2] Workers${NC}"
stop_all_workers
echo ""

# Step 2: Stop API
echo -e "${BOLD}[2/2] API${NC}"
stop_process "api"

print_footer
echo -e "  ${GREEN}Server stack stopped${NC}"
print_footer
