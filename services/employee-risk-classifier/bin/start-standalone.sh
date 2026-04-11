#!/usr/bin/env bash
#
# start-standalone.sh: 开发/演示模式下一键启动前后端
#

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
SERVICE_DIR="$(dirname "$DIR")"
BACKEND_PORT="${CLASSIFIER_PORT:-8010}"
FRONTEND_PORT="${CLASSIFIER_FRONTEND_PORT:-3001}"

echo "Starting Employee Risk Classifier Standalone Mode..."

# 确保有日志目录
mkdir -p "$SERVICE_DIR/var/logs" "$SERVICE_DIR/var/run"

# 停止可能遗留的旧进程
if [ -f "$SERVICE_DIR/bin/stop-standalone.sh" ]; then
    bash "$SERVICE_DIR/bin/stop-standalone.sh" >/dev/null 2>&1
fi

echo ">> Starting API backend server..."
nohup bash "$SERVICE_DIR/bin/employee-risk-server" > "$SERVICE_DIR/var/logs/server.log" 2>&1 < /dev/null &
SERVER_PID=$!
echo $SERVER_PID > "$SERVICE_DIR/var/run/server.pid"

echo ">> Starting Frontend UI..."
# 如果有 frontend package.json, 才尝试启动
if [ -d "$SERVICE_DIR/frontend" ]; then
    cd "$SERVICE_DIR/frontend"
    nohup npm run dev -- --host 127.0.0.1 --port "$FRONTEND_PORT" > "$SERVICE_DIR/var/logs/frontend.log" 2>&1 < /dev/null &
    FRONTEND_PID=$!
    echo $FRONTEND_PID > "$SERVICE_DIR/var/run/frontend.pid"
else
    echo "No frontend package found, skipping."
fi

echo "===================================="
echo "Standalone mode running!"
echo "Backend:  http://localhost:${BACKEND_PORT}"
if [ -d "$SERVICE_DIR/frontend" ]; then
    echo "Frontend: http://localhost:${FRONTEND_PORT}"
fi
echo "Look at var/logs/ for console outputs."
echo "Use 'bin/stop-standalone.sh' to stop."
echo "===================================="
