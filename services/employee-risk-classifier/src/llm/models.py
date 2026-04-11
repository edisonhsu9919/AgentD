"""
LLM相关的数据模型
"""

from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from enum import Enum

class LLMProvider(str, Enum):
    """LLM服务提供商"""
    LOCAL_VLLM = "local_vllm"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    QWEN = "qwen"
    CUSTOM = "custom"

class LLMConfig(BaseModel):
    """LLM配置模型"""
    provider: LLMProvider
    api_key: str
    base_url: str
    model: str
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    max_tokens: int = Field(default=512, gt=0)
    timeout: int = Field(default=60, gt=0)
    extra_params: Optional[Dict[str, Any]] = None

class LLMMessage(BaseModel):
    """LLM消息模型"""
    role: str = Field(..., description="消息角色：system, user, assistant")
    content: str = Field(..., description="消息内容")

class LLMRequest(BaseModel):
    """LLM请求模型"""
    messages: List[LLMMessage]
    config: Optional[LLMConfig] = None
    stream: bool = False

class LLMResponse(BaseModel):
    """LLM响应模型"""
    content: str = Field(..., description="生成的内容")
    provider: str = Field(..., description="使用的提供商")
    model: str = Field(..., description="使用的模型")
    usage: Optional[Dict[str, Any]] = None
    raw_response: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    
    @property
    def is_success(self) -> bool:
        """判断请求是否成功"""
        return self.error is None