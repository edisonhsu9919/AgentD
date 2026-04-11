"""
LLM提供商实现
"""

import asyncio
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse
import ipaddress
from openai import OpenAI, AsyncOpenAI
import httpx

from .models import LLMConfig, LLMMessage, LLMResponse, LLMProvider

class BaseLLMProvider(ABC):
    """LLM提供商基类"""
    
    def __init__(self, config: LLMConfig):
        self.config = config
    
    @abstractmethod
    async def generate(self, messages: List[LLMMessage]) -> LLMResponse:
        """生成响应"""
        pass
    
    @abstractmethod
    def generate_sync(self, messages: List[LLMMessage]) -> LLMResponse:
        """同步生成响应"""
        pass

class OpenAIProvider(BaseLLMProvider):
    """OpenAI兼容的提供商（支持本地vLLM、OpenAI、通义千问等）"""
    
    def __init__(self, config: LLMConfig):
        super().__init__(config)
        self.client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout
        )
        self.async_client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout
        )
    
    async def generate(self, messages: List[LLMMessage]) -> LLMResponse:
        """异步生成响应"""
        try:
            response = await self.async_client.chat.completions.create(
                model=self.config.model,
                messages=[{"role": msg.role, "content": msg.content} for msg in messages],
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                **(self.config.extra_params or {})
            )
            
            return LLMResponse(
                content=response.choices[0].message.content.strip(),
                provider=self.config.provider.value,
                model=self.config.model,
                usage=response.usage.model_dump() if response.usage else None,
                raw_response=response.model_dump()
            )
            
        except Exception as e:
            return LLMResponse(
                content="",
                provider=self.config.provider.value,
                model=self.config.model,
                error=str(e)
            )
    
    def generate_sync(self, messages: List[LLMMessage]) -> LLMResponse:
        """同步生成响应"""
        try:
            response = self.client.chat.completions.create(
                model=self.config.model,
                messages=[{"role": msg.role, "content": msg.content} for msg in messages],
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                **(self.config.extra_params or {})
            )
            
            return LLMResponse(
                content=response.choices[0].message.content.strip(),
                provider=self.config.provider.value,
                model=self.config.model,
                usage=response.usage.model_dump() if response.usage else None,
                raw_response=response.model_dump()
            )
            
        except Exception as e:
            return LLMResponse(
                content="",
                provider=self.config.provider.value,
                model=self.config.model,
                error=str(e)
            )

class AnthropicProvider(BaseLLMProvider):
    """Anthropic提供商（预留接口）"""
    
    async def generate(self, messages: List[LLMMessage]) -> LLMResponse:
        # TODO: 实现Anthropic API调用
        return LLMResponse(
            content="Anthropic provider not implemented yet",
            provider=self.config.provider.value,
            model=self.config.model,
            error="Not implemented"
        )
    
    def generate_sync(self, messages: List[LLMMessage]) -> LLMResponse:
        # TODO: 实现Anthropic API调用
        return LLMResponse(
            content="Anthropic provider not implemented yet",
            provider=self.config.provider.value,
            model=self.config.model,
            error="Not implemented"
        )

class CustomProvider(BaseLLMProvider):
    """自定义提供商（支持任意HTTP API）"""

    @staticmethod
    def _should_trust_env_proxy(base_url: str) -> bool:
        """
        对内网/本地地址默认禁用环境代理，避免公司代理影响局域网调用。
        """
        try:
            hostname = (urlparse(base_url).hostname or "").strip().lower()
            if not hostname:
                return True
            if hostname in {"localhost"}:
                return False

            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                return False
            return True
        except ValueError:
            # 非IP域名，维持原有行为（允许读取环境代理）
            return True
    
    async def generate(self, messages: List[LLMMessage]) -> LLMResponse:
        """通过HTTP API调用自定义服务"""
        try:
            trust_env = self._should_trust_env_proxy(self.config.base_url)
            timeout = httpx.Timeout(self.config.timeout)
            async with httpx.AsyncClient(timeout=timeout, trust_env=trust_env) as client:
                normalized_base_url = self.config.base_url.rstrip("/")
                endpoint = (
                    normalized_base_url
                    if normalized_base_url.endswith("/chat/completions")
                    else f"{normalized_base_url}/chat/completions"
                )
                payload = {
                    "messages": [{"role": msg.role, "content": msg.content} for msg in messages],
                    "model": self.config.model,
                    "temperature": self.config.temperature,
                    "max_tokens": self.config.max_tokens,
                    **(self.config.extra_params or {})
                }
                
                headers = {
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json"
                }
                
                response = await client.post(endpoint, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
                
                return LLMResponse(
                    content=data["choices"][0]["message"]["content"].strip(),
                    provider=self.config.provider.value,
                    model=self.config.model,
                    usage=data.get("usage"),
                    raw_response=data
                )
        except httpx.HTTPStatusError as e:
            response_preview = (e.response.text or "")[:500]
            return LLMResponse(
                content="",
                provider=self.config.provider.value,
                model=self.config.model,
                error=(
                    f"HTTP {e.response.status_code} from {e.request.url}: "
                    f"{response_preview}"
                )
            )
        except httpx.RequestError as e:
            request_url = str(e.request.url) if e.request else "unknown_url"
            detail = str(e).strip() or repr(e)
            return LLMResponse(
                content="",
                provider=self.config.provider.value,
                model=self.config.model,
                error=(
                    f"Request error ({e.__class__.__name__}) when calling "
                    f"{request_url}: {detail}"
                )
            )
                
        except Exception as e:
            detail = str(e).strip() or repr(e)
            return LLMResponse(
                content="",
                provider=self.config.provider.value,
                model=self.config.model,
                error=f"{e.__class__.__name__}: {detail}"
            )
    
    def generate_sync(self, messages: List[LLMMessage]) -> LLMResponse:
        """同步版本"""
        return asyncio.run(self.generate(messages))

# 提供商映射
PROVIDER_MAP = {
    LLMProvider.LOCAL_VLLM: OpenAIProvider,
    LLMProvider.OPENAI: OpenAIProvider,
    LLMProvider.QWEN: OpenAIProvider,
    LLMProvider.ANTHROPIC: AnthropicProvider,
    LLMProvider.CUSTOM: CustomProvider
}

def get_provider(config: LLMConfig) -> BaseLLMProvider:
    """根据配置获取提供商实例"""
    provider_class = PROVIDER_MAP.get(config.provider)
    if not provider_class:
        raise ValueError(f"Unsupported provider: {config.provider}")
    
    return provider_class(config)
