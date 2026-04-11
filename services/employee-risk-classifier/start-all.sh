#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ACTION="${1:-start}"
VENV_DIR="${VENV_DIR:-.venv}"
BACKEND_PORT="${BACKEND_PORT:-8010}"
FRONTEND_PORT="${FRONTEND_PORT:-3001}"
LOG_DIR="${ROOT_DIR}/logs"
RUN_DIR="${ROOT_DIR}/run"
PID_FILE="${RUN_DIR}/start-all.pids"

check_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "❌ 缺少命令: $1"
    exit 1
  fi
}

is_pid_running() {
  local pid="$1"
  [[ -n "${pid}" ]] && kill -0 "${pid}" >/dev/null 2>&1
}

stop_service_by_pid() {
  local name="$1"
  local pid="$2"
  if is_pid_running "${pid}"; then
    echo "🛑 停止 ${name} (PID=${pid})"
    kill "${pid}" >/dev/null 2>&1 || true
    sleep 1
    if is_pid_running "${pid}"; then
      echo "⚠️ ${name} 未正常退出，强制停止"
      kill -9 "${pid}" >/dev/null 2>&1 || true
    fi
  else
    echo "ℹ️ ${name} 已停止或PID不存在 (PID=${pid})"
  fi
}

start_services() {
  check_command python3
  check_command npm

  mkdir -p "${LOG_DIR}" "${RUN_DIR}"

  if [[ -f "${PID_FILE}" ]]; then
    # shellcheck disable=SC1090
    source "${PID_FILE}"
    if is_pid_running "${BACKEND_PID:-}" || is_pid_running "${FRONTEND_PID:-}"; then
      echo "⚠️ 检测到已有服务在运行："
      [[ -n "${BACKEND_PID:-}" ]] && echo "  BACKEND_PID=${BACKEND_PID}"
      [[ -n "${FRONTEND_PID:-}" ]] && echo "  FRONTEND_PID=${FRONTEND_PID}"
      echo "请先执行: ./start-all.sh stop"
      exit 1
    fi
  fi

  if [[ ! -d "${ROOT_DIR}/${VENV_DIR}" ]]; then
    echo "📦 创建虚拟环境: ${VENV_DIR}"
    python3 -m venv "${ROOT_DIR}/${VENV_DIR}"
  fi

  # shellcheck disable=SC1091
  source "${ROOT_DIR}/${VENV_DIR}/bin/activate"

  echo "📥 检查后端依赖..."
  if ! python -c "import fastapi, pydantic, pandas, openai" >/dev/null 2>&1; then
    echo "安装后端依赖 requirements.txt"
    python -m pip install -r "${ROOT_DIR}/requirements.txt"
  else
    echo "后端依赖已满足"
  fi

  echo "📥 检查前端依赖..."
  if [[ ! -d "${ROOT_DIR}/frontend/node_modules" ]]; then
    (cd "${ROOT_DIR}/frontend" && npm install)
  else
    echo "前端 node_modules 已存在，跳过 npm install"
  fi

  echo "🚀 启动后端..."
  CLASSIFIER_PORT="${BACKEND_PORT}" nohup "${ROOT_DIR}/${VENV_DIR}/bin/python" "${ROOT_DIR}/main.py" server >"${LOG_DIR}/backend.log" 2>&1 &
  BACKEND_PID=$!

  echo "🚀 启动前端..."
  cd "${ROOT_DIR}/frontend"
  nohup npm run dev -- --host 0.0.0.0 --port "${FRONTEND_PORT}" >"${LOG_DIR}/frontend.log" 2>&1 &
  FRONTEND_PID=$!
  cd "${ROOT_DIR}"

  sleep 2

  if ! is_pid_running "${BACKEND_PID}"; then
    echo "❌ 后端启动失败，查看日志: ${LOG_DIR}/backend.log"
    if is_pid_running "${FRONTEND_PID}"; then
      kill "${FRONTEND_PID}" 2>/dev/null || true
    fi
    exit 1
  fi

  if ! is_pid_running "${FRONTEND_PID}"; then
    echo "❌ 前端启动失败，查看日志: ${LOG_DIR}/frontend.log"
    kill "${BACKEND_PID}" 2>/dev/null || true
    exit 1
  fi

  cat >"${PID_FILE}" <<EOF
BACKEND_PID=${BACKEND_PID}
FRONTEND_PID=${FRONTEND_PID}
EOF

  echo ""
  echo "✅ 启动完成"
  echo "后端: http://localhost:${BACKEND_PORT}"
  echo "前端: http://localhost:${FRONTEND_PORT}"
  echo "日志: ${LOG_DIR}/backend.log, ${LOG_DIR}/frontend.log"
  echo "停止服务: ./start-all.sh stop"
}

stop_services() {
  if [[ ! -f "${PID_FILE}" ]]; then
    echo "ℹ️ 未找到PID文件，服务可能未通过本脚本启动。"
    return 0
  fi

  # shellcheck disable=SC1090
  source "${PID_FILE}"

  stop_service_by_pid "后端" "${BACKEND_PID:-}"
  stop_service_by_pid "前端" "${FRONTEND_PID:-}"
  rm -f "${PID_FILE}"
  echo "✅ 停止完成"
}

usage() {
  echo "用法: ./start-all.sh {start|stop|restart}"
}

case "${ACTION}" in
  start)
    start_services
    ;;
  stop)
    stop_services
    ;;
  restart)
    stop_services
    start_services
    ;;
  *)
    usage
    exit 1
    ;;
esac
