"""
LLM客户端 - 统一的LLM调用接口
"""

from typing import List, Optional
from .models import LLMConfig, LLMMessage, LLMResponse, LLMProvider
from .providers import get_provider, BaseLLMProvider
from config.settings import settings, get_llm_config

class LLMClient:
    """LLM客户端统一接口"""
    
    def __init__(self, config: Optional[LLMConfig] = None):
        """
        初始化LLM客户端
        
        Args:
            config: LLM配置，如果为None则使用默认配置
        """
        if config:
            self.config = config
        else:
            # 使用默认配置
            default_config = get_llm_config(settings.llm_provider)
            self.config = LLMConfig(
                provider=settings.llm_provider,
                api_key=settings.llm_api_key or default_config["api_key"],
                base_url=settings.llm_base_url or default_config["base_url"],
                model=settings.llm_model or default_config["model"],
                temperature=settings.llm_temperature,
                max_tokens=settings.llm_max_tokens,
                timeout=settings.llm_timeout
            )
        
        self.provider: BaseLLMProvider = get_provider(self.config)
    
    async def generate(self, messages: List[LLMMessage]) -> LLMResponse:
        """
        异步生成响应
        
        Args:
            messages: 消息列表
            
        Returns:
            LLM响应
        """
        return await self.provider.generate(messages)
    
    def generate_sync(self, messages: List[LLMMessage]) -> LLMResponse:
        """
        同步生成响应
        
        Args:
            messages: 消息列表
            
        Returns:
            LLM响应
        """
        return self.provider.generate_sync(messages)
    
    async def chat(self, system_prompt: str, user_message: str) -> LLMResponse:
        """
        简化的对话接口
        
        Args:
            system_prompt: 系统提示词
            user_message: 用户消息
            
        Returns:
            LLM响应
        """
        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_message)
        ]
        return await self.generate(messages)
    
    def chat_sync(self, system_prompt: str, user_message: str) -> LLMResponse:
        """
        同步对话接口
        
        Args:
            system_prompt: 系统提示词
            user_message: 用户消息
            
        Returns:
            LLM响应
        """
        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_message)
        ]
        return self.generate_sync(messages)
    
    def update_config(self, **kwargs) -> None:
        """
        更新配置
        
        Args:
            **kwargs: 配置参数
        """
        # 更新配置
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)
        
        # 重新创建提供商实例
        self.provider = get_provider(self.config)
    
    def switch_provider(self, provider: LLMProvider, **config_override) -> None:
        """
        切换LLM提供商
        
        Args:
            provider: 新的提供商
            **config_override: 配置覆盖参数
        """
        # 获取新提供商的默认配置
        default_config = get_llm_config(provider)
        
        # 创建新配置
        new_config = LLMConfig(
            provider=provider,
            api_key=config_override.get("api_key", default_config["api_key"]),
            base_url=config_override.get("base_url", default_config["base_url"]),
            model=config_override.get("model", default_config["model"]),
            temperature=config_override.get("temperature", self.config.temperature),
            max_tokens=config_override.get("max_tokens", self.config.max_tokens),
            timeout=config_override.get("timeout", self.config.timeout),
            extra_params=config_override.get("extra_params")
        )
        
        self.config = new_config
        self.provider = get_provider(self.config)
    
    def get_config_info(self) -> dict:
        """获取当前配置信息"""
        return {
            "provider": self.config.provider.value,
            "api_key": self.config.api_key,
            "model": self.config.model,
            "base_url": self.config.base_url,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "timeout": self.config.timeout
        }
