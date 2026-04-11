#!/bin/bash
# 停止演示服务脚本

echo "🛑 停止分类通-AI分类助手演示服务..."

if [ -f "demo-pids.txt" ]; then
    while read pid; do
        if ps -p $pid > /dev/null 2>&1; then
            echo "停止进程 $pid"
            kill $pid
        else
            echo "进程 $pid 已经停止"
        fi
    done < demo-pids.txt
    
    rm demo-pids.txt
    echo "✅ 服务已停止"
else
    echo "❌ 未找到PID文件，手动查找进程..."
    
    # 查找并停止可能的进程
    pkill -f "python main.py server"
    pkill -f "npx serve"
    
    echo "✅ 已尝试停止相关进程"
fi

echo "🧹 清理日志文件..."
rm -f backend.log frontend.log

echo "✨ 清理完成！"