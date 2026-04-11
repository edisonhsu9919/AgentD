"""
应用配置管理模块
"""

import os
from pathlib import Path
from typing import Dict, Any, Optional
from pydantic_settings import BaseSettings
from pydantic import field_validator
from enum import Enum

class LLMProvider(str, Enum):
    """LLM服务提供商枚举"""
    LOCAL_VLLM = "local_vllm"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    QWEN = "qwen"
    CUSTOM = "custom"

class ClassificationTask(str, Enum):
    """分类任务类型枚举"""
    RISK_ASSESSMENT = "risk_assessment"
    INDUSTRY_CLASSIFICATION = "industry_classification"
    SKILL_ANALYSIS = "skill_analysis"
    CUSTOM = "custom"

class Settings(BaseSettings):
    """应用配置类"""
    
    # 应用基本配置
    app_name: str = "智能分类器"
    app_version: str = "2.0.0"
    debug: bool = False
    
    # 服务器配置
    host: str = "0.0.0.0"
    port: int = 8010
    
    # LLM配置
    llm_provider: LLMProvider = LLMProvider.LOCAL_VLLM
    llm_api_key: str = "vllm"
    llm_base_url: str = "http://localhost:8000/v1"
    llm_model: str = "/root/autodl-tmp/models/Qwen3-8b-AWQ"
    llm_temperature: float = 0.1
    llm_max_tokens: int = 512
    llm_timeout: int = 60
    
    # 分类任务配置
    default_task: ClassificationTask = ClassificationTask.RISK_ASSESSMENT
    
    # 文件处理配置
    max_file_size: int = 10 * 1024 * 1024  # 10MB
    allowed_file_types: list = [".xlsx", ".xls", ".csv"]
    upload_path: str = "uploads"
    output_path: str = "outputs"
    
    # 数据库配置（预留）
    database_url: Optional[str] = None
    
    # 缓存配置
    enable_cache: bool = True
    cache_ttl: int = 3600  # 1小时
    
    @field_validator('upload_path', 'output_path')
    def create_directories(cls, v):
        """确保目录存在"""
        Path(v).mkdir(parents=True, exist_ok=True)
        return v
    
    model_config = {
        "env_file": ".env",
        "env_prefix": "CLASSIFIER_",
        "extra": "ignore"  # 忽略额外字段
    }

# 全局配置实例
settings = Settings()

# LLM提供商配置映射
LLM_CONFIGS = {
    LLMProvider.LOCAL_VLLM: {
        "api_key": "vllm",
        "base_url": "http://localhost:8000/v1",
        "model": "/root/autodl-tmp/models/Qwen3-8b-AWQ"
    },
    LLMProvider.OPENAI: {
        "api_key": os.getenv("OPENAI_API_KEY", ""),
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-3.5-turbo"
    },
    LLMProvider.ANTHROPIC: {
        "api_key": os.getenv("ANTHROPIC_API_KEY", ""),
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-3-haiku-20240307"
    },
    LLMProvider.QWEN: {
        "api_key": os.getenv("QWEN_API_KEY", ""),
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-turbo"
    }
}

def get_llm_config(provider: LLMProvider) -> Dict[str, Any]:
    """获取指定LLM提供商的配置"""
    return LLM_CONFIGS.get(provider, LLM_CONFIGS[LLMProvider.LOCAL_VLLM])

def update_llm_config(provider: LLMProvider, **kwargs) -> None:
    """更新LLM配置"""
    if provider in LLM_CONFIGS:
        LLM_CONFIGS[provider].update(kwargs)
    else:
        LLM_CONFIGS[provider] = kwargs
