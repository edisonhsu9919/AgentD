# 分类通-AI分类助手 部署指南

## 快速部署

### 1. 上传并解压
```bash
scp classifier-demo.tar.gz user@服务器IP:/home/user/
ssh user@服务器IP
tar -xzf classifier-demo.tar.gz && cd employee_risk_classify
```

### 2. 安装环境
```bash
# 创建conda环境
conda create -n employee_classifier python=3.10 -y
conda activate employee_classifier

# 安装依赖
pip install -r requirements.txt
cd frontend && npm install && cd ..
```

### 3. 启动服务
```bash
./deploy-demo.sh
```

### 4. 访问系统
- **系统入口**: `http://服务器IP:3001`
- **演示页面**: 打开 `demo-info.html` 查看二维码

## 演示流程

1. 运行 `./deploy-demo.sh` 启动服务
2. 打开 `demo-info.html` 展示二维码给观众
3. 观众扫码进入系统体验功能
4. 演示结束运行 `./stop-demo.sh` 停止服务

## 故障排除

- **无法访问**: 检查端口3001和8010是否开放
- **启动失败**: 查看 `backend.log` 和 `frontend.log` 
- **重新部署**: 运行 `./pack-project.sh` 重新打包

---
**服务器要求**: 2核4GB+ | **端口**: 3001,8010 | **支持移动端**
