#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# AgentD — Stop all stack processes
# ═══════════════════════════════════════════════════════════════════════════════
#
# Stop order (reverse of start):
#   1. Frontend
#   2. Workers (all)
#   3. API
#
# Each process: SIGTERM → wait 10s → SIGKILL
#
# Usage:
#   scripts/dev/stop_stack.sh

source "$(dirname "$0")/../lib/common.sh"

print_banner "Stopping Stack"

# Step 1: Stop frontend
echo -e "${BOLD}[1/3] Frontend${NC}"
stop_process "web"
echo ""

# Step 2: Stop all workers
echo -e "${BOLD}[2/3] Workers${NC}"
stop_all_workers
echo ""

# Step 3: Stop API
echo -e "${BOLD}[3/3] API${NC}"
stop_process "api"

print_footer
echo -e "  ${GREEN}All processes stopped${NC}"
print_footer
