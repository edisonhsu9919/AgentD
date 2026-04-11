# 智能职业分类系统 v2.0

一个基于AI的多功能职业分类系统，支持职业风险评估、行业分类、技能分析等多种分类任务。系统采用模块化架构，提供RESTful API和命令行界面，支持多种LLM提供商。

## 🚀 核心特性

### 多分类任务支持
- **职业风险评估**: 6级风险分类（1-6类）
- **行业分类**: 20种国标行业分类（A-T类）
- **技能分析**: 5级技能要求分析（初级-管理级）
- **自定义分类**: 支持用户自定义分类模板

### 多LLM提供商支持
- **本地部署**: vLLM本地模型服务
- **OpenAI**: GPT系列模型
- **通义千问**: 阿里云大模型服务
- **自定义**: 兼容OpenAI格式的任意API

### 灵活的使用方式
- **RESTful API**: 完整的Web API服务
- **命令行工具**: 便捷的CLI接口
- **批量处理**: 支持Excel/CSV文件批量处理
- **实时分类**: 单条数据即时分类

## 📦 快速开始

### 环境准备

```bash
# 克隆项目
git clone <repository_url>
cd employee_risk_classify

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env 文件配置你的LLM提供商信息
```

### 启动API服务

```bash
# 启动服务器
python main.py server

# 访问API文档
# http://localhost:8010/docs
```

### 一键启动前后端（推荐）

```bash
# 启动（自动处理虚拟环境与依赖）
./start-all.sh start

# 停止
./start-all.sh stop

# 重启
./start-all.sh restart
```

默认端口：前端 `3001`，后端 `8010`。

### 命令行使用

```bash
# 单条分类
python main.py single "软件工程师" -c "科技公司" -t "risk_assessment"

# 批量处理文件
python main.py batch input.xlsx output.xlsx -t "risk_assessment"

# 查看可用分类任务
python main.py tasks

# 测试LLM连接
python main.py test
```

## 📋 分类任务详情

### 1. 职业风险评估 (risk_assessment)
基于作业环境和工作内容的6级风险分类：

- **1类**: 极低风险 - 办公室岗位
- **2类**: 低风险 - 辅助性岗位  
- **3类**: 中等风险 - 农业自然作业类
- **4类**: 中高风险 - 工厂基础危险岗位
- **5类**: 高风险 - 有毒有害化工类
- **6类**: 极高风险 - 矿井爆破高空作业

### 2. 行业分类 (industry_classification)
基于国民经济行业分类标准的20类行业归类：

- **A类**: 农、林、牧、渔业
- **B类**: 采矿业
- **C类**: 制造业
- **...** (完整20类)
- **T类**: 国际组织

### 3. 技能分析 (skill_analysis)
基于工作复杂度和技能要求的5级分析：

- **初级**: 基础技能要求
- **中级**: 专业技能要求
- **高级**: 高级专业技能
- **专家级**: 领域专家级技能
- **管理级**: 管理和领导技能

## 🔧 API接口

### 核心接口

```bash
# 单条分类
POST /api/v1/classification/single
{
    "job_title": "软件工程师",
    "company_name": "科技公司",
    "task_type": "risk_assessment"
}

# 批量分类
POST /api/v1/classification/batch
{
    "items": [...],
    "task_type": "risk_assessment"
}

# 文件上传处理
POST /api/v1/file/upload
# 支持 Excel/CSV 文件上传

# 获取处理状态
GET /api/v1/file/status/{task_id}

# 下载处理结果
GET /api/v1/file/download/{task_id}
```

### 配置管理

```bash
# 获取/更新LLM配置
GET/POST /api/v1/llm/config

# 管理分类模板
GET /api/v1/templates
POST /api/v1/templates/custom
DELETE /api/v1/templates/custom/{name}
```

## ⚙️ 配置说明

### 环境变量配置

```bash
# LLM提供商配置
CLASSIFIER_LLM_PROVIDER=local_vllm  # local_vllm|openai|qwen|custom
CLASSIFIER_LLM_BASE_URL=http://localhost:8000/v1
CLASSIFIER_LLM_MODEL=model_name
CLASSIFIER_LLM_API_KEY=your_api_key

# 服务器配置
CLASSIFIER_HOST=0.0.0.0
CLASSIFIER_PORT=8010

# 文件处理配置
CLASSIFIER_UPLOAD_PATH=uploads
CLASSIFIER_OUTPUT_PATH=outputs
CLASSIFIER_MAX_FILE_SIZE=10485760  # 10MB
```

### LLM提供商配置

#### 本地vLLM (推荐)
```bash
CLASSIFIER_LLM_PROVIDER=local_vllm
CLASSIFIER_LLM_BASE_URL=http://localhost:8000/v1
CLASSIFIER_LLM_MODEL=/root/autodl-tmp/models/Qwen3-8b-AWQ
```

#### OpenAI
```bash
CLASSIFIER_LLM_PROVIDER=openai
OPENAI_API_KEY=your_openai_api_key
CLASSIFIER_LLM_MODEL=gpt-3.5-turbo
```

#### 通义千问
```bash
CLASSIFIER_LLM_PROVIDER=qwen
QWEN_API_KEY=your_qwen_api_key
CLASSIFIER_LLM_MODEL=qwen-turbo
```

## 📁 项目结构

```
employee_risk_classify/
├── src/                    # 源代码
│   ├── api/               # FastAPI接口
│   ├── core/              # 核心分类逻辑
│   ├── llm/               # LLM集成层
│   └── prompts/           # 提示词管理
├── config/                # 配置管理
├── archive/               # 原型和历史版本
├── uploads/               # 文件上传目录
├── outputs/               # 处理结果目录
├── main.py               # 主程序入口
├── requirements.txt      # 依赖包
└── .env.example         # 环境配置示例
```

## 📊 输入输出格式

### 输入Excel/CSV格式
```csv
岗位名称,公司名称
软件工程师,科技公司
电焊工,制造企业
会计,金融机构
```

### 输出格式
```csv
岗位名称,公司名称,分类结果,分类理由,处理状态,处理时间
软件工程师,科技公司,1类,办公室工作无高风险接触,成功,0.5秒
电焊工,制造企业,4类,涉及高温和机械操作风险,成功,0.6秒
```

## 🛠️ 开发指南

### 添加自定义分类任务

1. 创建新的提示词模板类
2. 在PromptManager中注册
3. 通过API或配置文件定义分类等级

### 扩展LLM提供商

1. 继承BaseLLMProvider类
2. 实现generate方法
3. 在PROVIDER_MAP中注册

## 📈 性能优化

- **并发处理**: 批量任务支持可配置并发数
- **异步架构**: FastAPI异步处理提升响应速度
- **缓存机制**: 支持结果缓存减少重复计算
- **错误恢复**: 单项失败不影响批量处理

## 🔍 故障排除

### 常见问题

1. **LLM连接失败**: 检查API配置和网络连接
2. **文件处理超时**: 调整并发数或分批处理
3. **内存不足**: 适当减少批处理大小

### 调试模式

```bash
# 启用调试模式
CLASSIFIER_DEBUG=true python main.py server
```

## 📝 更新日志

### v2.0.0 (当前版本)
- 🎉 全新模块化架构
- 🚀 多分类任务支持
- 🔌 多LLM提供商支持
- 🌐 完整RESTful API
- ⚡ 异步批处理
- 🎨 自定义模板系统

### v1.x (已归档)
- 基础批处理功能
- GUI界面
- 单一风险评估任务

## 📄 许可证

[MIT License](LICENSE)

## 🤝 贡献

欢迎提交Issue和Pull Request来改进项目！

## 📞 支持

如有问题，请通过以下方式联系：
- 提交 [GitHub Issue](issues)
- 查看 [API文档](http://localhost:8010/docs)
- 阅读 [开发指南](CLAUDE.md)
