#!/usr/bin/env bash
#
# stop-standalone.sh
#

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
SERVICE_DIR="$(dirname "$DIR")"
BACKEND_PORT="${CLASSIFIER_PORT:-8010}"
FRONTEND_PORT="${CLASSIFIER_FRONTEND_PORT:-3001}"

echo "Stopping Employee Risk Classifier Standalone Mode..."

if [ -f "$SERVICE_DIR/var/run/server.pid" ]; then
    SERVER_PID=$(cat "$SERVICE_DIR/var/run/server.pid")
    echo "Killing backend server (PID $SERVER_PID)..."
    kill $SERVER_PID 2>/dev/null || true
    rm "$SERVICE_DIR/var/run/server.pid"
fi

if [ -f "$SERVICE_DIR/var/run/frontend.pid" ]; then
    FRONTEND_PID=$(cat "$SERVICE_DIR/var/run/frontend.pid")
    echo "Killing frontend UI (PID $FRONTEND_PID)..."
    kill $FRONTEND_PID 2>/dev/null || true
    rm "$SERVICE_DIR/var/run/frontend.pid"
fi

# Extra cleanup fallback matching port to prevent zombies
echo "Cleaning up lingering processes on ${BACKEND_PORT}/${FRONTEND_PORT}..."
lsof -t -i:"$BACKEND_PORT" | xargs kill -9 2>/dev/null || true
lsof -t -i:"$FRONTEND_PORT" | xargs kill -9 2>/dev/null || true

echo "Stopped."
