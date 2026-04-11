#!/bin/bash
# 项目打包脚本

echo "📦 开始打包分类通-AI分类助手项目..."

# 清理临时文件
echo "🧹 清理临时文件..."
find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find . -name "*.pyc" -delete 2>/dev/null || true
find . -name "*.log" -delete 2>/dev/null || true
rm -rf frontend/dist 2>/dev/null || true
rm -f *.tar.gz 2>/dev/null || true

# 移到上级目录打包
cd ..

# 创建打包
echo "📁 创建压缩包..."
COPYFILE_DISABLE=1 tar --exclude='employee_risk_classify/node_modules' \
    --exclude='employee_risk_classify/__pycache__' \
    --exclude='employee_risk_classify/*.pyc' \
    --exclude='employee_risk_classify/.git' \
    --exclude='employee_risk_classify/*.log' \
    --exclude='employee_risk_classify/frontend/dist' \
    --exclude='employee_risk_classify/frontend/node_modules' \
    --exclude='employee_risk_classify/outputs' \
    --exclude='employee_risk_classify/uploads' \
    --exclude='employee_risk_classify/*.tar.gz' \
    --exclude='employee_risk_classify/.DS_Store' \
    --exclude='employee_risk_classify/frontend/.DS_Store' \
    --exclude='employee_risk_classify/DEMO-SETUP.md' \
    --exclude='employee_risk_classify/DEPLOYMENT-CHECKLIST.md' \
    -czf classifier-demo.tar.gz employee_risk_classify/ 2>/dev/null

# 移动压缩包到项目目录
mv classifier-demo.tar.gz employee_risk_classify/

cd employee_risk_classify

# 显示打包结果
echo ""
echo "✅ 打包完成！"
echo "📁 压缩包: classifier-demo.tar.gz"
echo "📊 文件大小: $(du -h classifier-demo.tar.gz | cut -f1)"
echo ""
echo "🚀 接下来步骤："
echo "1. 上传到服务器: scp classifier-demo.tar.gz user@server:/path/"
echo "2. 服务器解压: tar -xzf classifier-demo.tar.gz"
echo "3. 运行部署脚本: ./deploy-demo.sh"