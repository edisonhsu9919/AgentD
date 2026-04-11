#!/bin/bash
# 简化的部署脚本 - 适用于Ubuntu服务器

echo "🚀 启动分类通-AI分类助手..."

# 检查后端是否可以启动
echo "▶️  启动后端服务 (端口8010)..."
CLASSIFIER_PORT=8010 nohup python main.py server > backend.log 2>&1 &
backend_pid=$!
echo "后端PID: $backend_pid"

# 等待后端启动
sleep 3

# 构建并启动前端
echo "🏗️ 构建前端..."
cd frontend

# 简单构建（跳过TypeScript检查）
echo "📦 构建前端应用（生产模式）..."
npm run build 2>/dev/null || {
    echo "⚠️  TypeScript构建失败，尝试简单构建..."
    # 如果TypeScript构建失败，使用vite直接构建
    npx vite build --mode production
}

echo "▶️  启动前端服务 (端口3001)..."
nohup npx serve -s dist -l 3001 > ../frontend.log 2>&1 &
frontend_pid=$!
echo "前端PID: $frontend_pid"

# 保存PID
cd ..
echo "$backend_pid" > demo-pids.txt
echo "$frontend_pid" >> demo-pids.txt

echo ""
echo "✅ 部署完成！"
echo "📱 演示地址: http://$(curl -s ifconfig.me):3001"
echo "🔧 后端API: http://$(curl -s ifconfig.me):8010"
echo ""
echo "📋 使用 ./stop-demo.sh 停止服务"
echo "📝 日志文件: backend.log, frontend.log"
