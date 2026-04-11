#!/bin/bash
# 演示部署脚本

echo "🚀 开始部署分类通-AI分类助手演示版..."

# 检查环境
if ! command -v conda &> /dev/null; then
    echo "❌ 需要安装conda环境"
    exit 1
fi

if ! command -v npm &> /dev/null; then
    echo "❌ 需要安装npm"
    exit 1
fi

# 激活conda环境
echo "📦 激活conda环境..."
# 尝试不同的conda路径
if [ -f ~/anaconda3/etc/profile.d/conda.sh ]; then
    source ~/anaconda3/etc/profile.d/conda.sh
elif [ -f ~/miniconda3/etc/profile.d/conda.sh ]; then
    source ~/miniconda3/etc/profile.d/conda.sh
elif [ -f /opt/conda/etc/profile.d/conda.sh ]; then
    source /opt/conda/etc/profile.d/conda.sh
else
    echo "⚠️  未找到conda，请手动激活环境: conda activate employee_classifier"
fi

# 尝试激活环境
conda activate employee_classifier 2>/dev/null || echo "⚠️  请确保已创建employee_classifier环境"

# 构建前端
echo "🏗️ 构建前端应用..."
cd frontend

# 检查并安装前端依赖
if [ ! -d "node_modules" ]; then
    echo "📦 安装前端依赖..."
    npm install
fi

# 检查是否有TypeScript编译器
if ! npm list vue-tsc &>/dev/null; then
    echo "📦 安装TypeScript编译器..."
    npm install --save-dev vue-tsc typescript
fi

# 构建前端
npm run build

# 全局安装serve（如果还没安装）
if ! command -v serve &> /dev/null; then
    echo "📦 安装serve工具..."
    npm install -g serve
fi

# 启动服务
echo "🔄 启动服务..."

# 后台启动后端
echo "▶️  启动后端服务 (端口8010)..."
cd ..
CLASSIFIER_PORT=8010 nohup python main.py server > backend.log 2>&1 &
backend_pid=$!
echo "后端PID: $backend_pid"

# 等待后端启动
sleep 3

# 启动前端服务
echo "▶️  启动前端服务 (端口3001)..."
cd frontend
nohup npx serve -s dist -l 3001 > ../frontend.log 2>&1 &
frontend_pid=$!
echo "前端PID: $frontend_pid"

# 保存PID到文件
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
