"""
LLM配置持久化存储
"""

import json
from pathlib import Path
from typing import Dict, Tuple, Optional

from .models import LLMConfig, LLMProvider
from config.settings import settings, get_llm_config


class LLMConfigStore:
    """基于本地JSON文件的LLM配置档案存储"""

    def __init__(self, store_path: Optional[Path] = None):
        self.store_path = store_path or Path("config/llm_profiles.json")
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_store_initialized()

    def _default_config(self) -> LLMConfig:
        provider_defaults = get_llm_config(settings.llm_provider)
        api_key = settings.llm_api_key or provider_defaults.get("api_key", "")
        base_url = settings.llm_base_url or provider_defaults.get("base_url", "")
        model = settings.llm_model or provider_defaults.get("model", "")

        return LLMConfig(
            provider=settings.llm_provider,
            api_key=api_key,
            base_url=base_url,
            model=model,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            timeout=settings.llm_timeout,
        )

    def _empty_store_payload(self) -> Dict:
        default_config = self._default_config()
        return {
            "active_profile": "default",
            "profiles": {
                "default": self.serialize_config(default_config),
            },
        }

    def _ensure_store_initialized(self) -> None:
        if not self.store_path.exists():
            self._write_payload(self._empty_store_payload())
            return

        try:
            payload = self._read_payload()
            if "profiles" not in payload or not payload["profiles"]:
                self._write_payload(self._empty_store_payload())
                return
            if payload.get("active_profile") not in payload["profiles"]:
                first_profile = next(iter(payload["profiles"].keys()))
                payload["active_profile"] = first_profile
                self._write_payload(payload)
        except Exception:
            # 文件损坏时回退为默认配置
            self._write_payload(self._empty_store_payload())

    def _read_payload(self) -> Dict:
        with self.store_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _write_payload(self, payload: Dict) -> None:
        with self.store_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    @staticmethod
    def serialize_config(config: LLMConfig) -> Dict:
        return {
            "provider": config.provider.value,
            "api_key": config.api_key,
            "base_url": config.base_url,
            "model": config.model,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "timeout": config.timeout,
            "extra_params": config.extra_params,
        }

    @staticmethod
    def deserialize_config(config_data: Dict) -> LLMConfig:
        return LLMConfig(
            provider=LLMProvider(config_data["provider"]),
            api_key=config_data.get("api_key", ""),
            base_url=config_data.get("base_url", ""),
            model=config_data.get("model", ""),
            temperature=config_data.get("temperature", 0.1),
            max_tokens=config_data.get("max_tokens", 512),
            timeout=config_data.get("timeout", 60),
            extra_params=config_data.get("extra_params"),
        )

    def get_active_profile(self) -> Tuple[str, LLMConfig]:
        payload = self._read_payload()
        active_name = payload["active_profile"]
        active_config = self.deserialize_config(payload["profiles"][active_name])
        return active_name, active_config

    def list_profiles(self) -> Dict[str, LLMConfig]:
        payload = self._read_payload()
        return {
            name: self.deserialize_config(config_data)
            for name, config_data in payload["profiles"].items()
        }

    def save_profile(self, name: str, config: LLMConfig, set_active: bool = False) -> None:
        profile_name = name.strip()
        if not profile_name:
            raise ValueError("配置名称不能为空")

        payload = self._read_payload()
        payload["profiles"][profile_name] = self.serialize_config(config)
        if set_active:
            payload["active_profile"] = profile_name
        self._write_payload(payload)

    def delete_profile(self, name: str) -> bool:
        payload = self._read_payload()
        if name not in payload["profiles"]:
            return False

        if len(payload["profiles"]) == 1:
            raise ValueError("至少需要保留一个配置档案")

        del payload["profiles"][name]
        if payload["active_profile"] == name:
            payload["active_profile"] = next(iter(payload["profiles"].keys()))
        self._write_payload(payload)
        return True

    def set_active_profile(self, name: str) -> LLMConfig:
        payload = self._read_payload()
        if name not in payload["profiles"]:
            raise ValueError(f"配置档案不存在: {name}")
        payload["active_profile"] = name
        self._write_payload(payload)
        return self.deserialize_config(payload["profiles"][name])

    def get_profile(self, name: str) -> LLMConfig:
        payload = self._read_payload()
        if name not in payload["profiles"]:
            raise ValueError(f"配置档案不存在: {name}")
        return self.deserialize_config(payload["profiles"][name])
