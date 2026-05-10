"""Maintenance sidecar model resolver and invocation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from agent.provider_reasoning import (
    ProviderAwareChatOpenAI,
    build_chatopenai_reasoning_kwargs,
    resolve_provider_family,
)


@dataclass(frozen=True)
class ResolvedMaintenanceModelConfig:
    purpose: str
    source: str
    name: str
    base_url: str
    api_key: str
    model_id: str
    provider_type: str = "openai_compatible"
    timeout_seconds: int | None = None
    extra_params: dict[str, Any] | None = None
    disabled: bool = False
    disabled_reason: str | None = None


async def resolve_maintenance_model_config(
    db: AsyncSession,
    purpose: str,
) -> ResolvedMaintenanceModelConfig:
    """Resolve a short-task maintenance model without inheriting main-agent params."""
    from model_config.service import resolve_active_model_config, resolve_active_vlm_config

    vlm = await resolve_active_vlm_config(db)
    if vlm is not None:
        return ResolvedMaintenanceModelConfig(
            purpose=purpose,
            source=f"{vlm.source}:vlm",
            name=vlm.name,
            base_url=vlm.base_url,
            api_key=vlm.api_key,
            model_id=vlm.model_id,
            provider_type=getattr(vlm, "provider_type", "openai_compatible"),
            timeout_seconds=vlm.timeout_seconds,
            extra_params=dict(getattr(vlm, "extra_params", None) or {}),
        )

    llm = await resolve_active_model_config(db)
    if llm is not None:
        return ResolvedMaintenanceModelConfig(
            purpose=purpose,
            source=f"{llm.source}:llm",
            name=llm.name,
            base_url=llm.base_url,
            api_key=llm.api_key,
            model_id=llm.model_id,
            provider_type=llm.provider_type,
            timeout_seconds=llm.timeout_seconds,
            extra_params=dict(getattr(llm, "extra_params", None) or {}),
        )

    return ResolvedMaintenanceModelConfig(
        purpose=purpose,
        source="disabled",
        name="Maintenance Disabled",
        base_url="",
        api_key="",
        model_id="",
        disabled=True,
        disabled_reason="no_active_model_config",
    )


def maintenance_chat_kwargs(
    resolved: ResolvedMaintenanceModelConfig,
    *,
    purpose: str,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Build provider kwargs for short sidecar calls with thinking disabled."""
    token_budget = max_tokens or _default_max_tokens(purpose)
    params: dict[str, Any] = dict(resolved.extra_params or {})
    params.setdefault("temperature", 0.2)
    params["max_tokens"] = token_budget
    params.setdefault("top_p", 0.8)
    params["enable_thinking"] = False
    params["preserve_thinking"] = False
    chat_template_kwargs = dict(params.get("chat_template_kwargs") or {})
    chat_template_kwargs.update({
        "enable_thinking": False,
        "preserve_thinking": False,
    })
    params["chat_template_kwargs"] = chat_template_kwargs
    return build_chatopenai_reasoning_kwargs(_ResolvedMaintenanceRequest(
        resolved=resolved,
        extra_params=params,
    ))


def maintenance_kwargs_shape(kwargs: dict[str, Any]) -> dict[str, list[str]]:
    """Return a redacted shape of provider kwargs for diagnostics."""
    shape: dict[str, list[str]] = {
        "top_level": sorted(str(key) for key in kwargs.keys()),
    }
    for key in ("model_kwargs", "extra_body"):
        value = kwargs.get(key)
        if isinstance(value, dict):
            shape[key] = sorted(str(child_key) for child_key in value.keys())
    return shape


async def invoke_maintenance_chat(
    db: AsyncSession,
    *,
    purpose: str,
    messages: list,
    max_tokens: int | None = None,
):
    """Invoke the resolved maintenance model via OpenAI-compatible chat."""
    resolved = await resolve_maintenance_model_config(db, purpose)
    if resolved.disabled:
        return None, resolved

    async with httpx.AsyncClient(trust_env=False) as http_client:
        kwargs = maintenance_chat_kwargs(
            resolved, purpose=purpose, max_tokens=max_tokens,
        )
        provider_family = resolve_provider_family(
            resolved.provider_type, resolved.base_url, resolved.model_id,
        )
        llm = ProviderAwareChatOpenAI(
            model=resolved.model_id,
            base_url=resolved.base_url,
            api_key=resolved.api_key,
            streaming=False,
            http_async_client=http_client,
            provider_family=provider_family,
            **kwargs,
        )
        result = await llm.ainvoke(messages)
    return result, resolved


class _ResolvedMaintenanceRequest:
    def __init__(
        self,
        *,
        resolved: ResolvedMaintenanceModelConfig,
        extra_params: dict[str, Any],
    ) -> None:
        self.source = resolved.source
        self.name = resolved.name
        self.base_url = resolved.base_url
        self.api_key = resolved.api_key
        self.model_id = resolved.model_id
        self.provider_type = resolved.provider_type
        self.timeout_seconds = resolved.timeout_seconds
        self.extra_params = extra_params


def _default_max_tokens(purpose: str) -> int:
    if purpose == "title":
        return 256
    if purpose == "session_memory":
        return 4096
    if purpose == "compact":
        return 3000
    return 1024
