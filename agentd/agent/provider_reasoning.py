"""Provider-aware reasoning helpers for v0.4.3.

Separates user-visible reasoning text from provider continuation state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    ChatMessage,
    FunctionMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_openai import ChatOpenAI
from langchain_openai.chat_models.base import (
    _format_message_content,
    _lc_invalid_tool_call_to_openai_tool_call,
    _lc_tool_call_to_openai_tool_call,
)


_REASONING_KEYS = (
    "reasoning_content",
    "reasoning",
    "thinking",
    "reasoning_details",
)
_PROVIDER_CONTINUATION_KEYS = {
    "deepseek": ("reasoning_content",),
    "qwen": ("reasoning_content", "thinking"),
    "glm": ("reasoning_content",),
    "minimax": ("reasoning_content", "reasoning_details"),
}


class TranscriptIntegrityError(RuntimeError):
    """Raised when provider payload would violate tool-call adjacency."""

    def __init__(self, issues: list[dict[str, Any]]):
        self.code = "TRANSCRIPT_INTEGRITY_ERROR"
        self.issues = issues
        super().__init__(
            "TRANSCRIPT_INTEGRITY_ERROR: provider payload has invalid "
            f"tool-call adjacency: {issues}"
        )


@dataclass(frozen=True)
class ReasoningExtraction:
    visible_text: str = ""
    provider_state: dict[str, Any] | None = None
    source: str = ""


def build_chatopenai_reasoning_kwargs(resolved_model_config) -> dict[str, Any]:
    """Translate model_config.extra_params into ChatOpenAI constructor kwargs."""
    extra_params = dict(getattr(resolved_model_config, "extra_params", None) or {})
    provider_family = resolve_provider_family(
        getattr(resolved_model_config, "provider_type", ""),
        getattr(resolved_model_config, "base_url", ""),
        getattr(resolved_model_config, "model_id", ""),
    )
    extra_params = _normalize_reasoning_request_params(provider_family, extra_params)
    strict_provider_compat_fallback = bool(
        extra_params.pop("strict_provider_compat_fallback", False)
    )
    model_kwargs = dict(extra_params.pop("model_kwargs", {}) or {})
    extra_body = dict(extra_params.pop("extra_body", {}) or {})

    # Any remaining top-level provider params flow through model_kwargs.
    for key, value in extra_params.items():
        model_kwargs[key] = value

    kwargs: dict[str, Any] = {}
    timeout_seconds = getattr(resolved_model_config, "timeout_seconds", None)
    if timeout_seconds:
        kwargs["timeout"] = timeout_seconds
    if model_kwargs:
        kwargs["model_kwargs"] = model_kwargs
    if extra_body:
        kwargs["extra_body"] = extra_body
    kwargs["strict_provider_compat_fallback"] = strict_provider_compat_fallback
    return kwargs


def resolve_provider_family(provider_type: str, base_url: str, model_id: str) -> str:
    """Resolve a concrete provider family from config + simple heuristics."""
    raw = " ".join(filter(None, [provider_type, base_url, model_id])).lower()
    if "deepseek" in raw:
        return "deepseek"
    if "qwen" in raw or "dashscope" in raw:
        return "qwen"
    if "glm" in raw or "bigmodel" in raw or "zhipu" in raw:
        return "glm"
    if "minimax" in raw:
        return "minimax"
    return provider_type or "openai_compatible"


class ProviderAwareChatOpenAI(ChatOpenAI):
    """ChatOpenAI variant that preserves provider continuation reasoning state."""

    provider_family: str = "openai_compatible"
    strict_provider_compat_fallback: bool = False

    def _create_chat_result(self, response: Any, generation_info: dict | None = None):
        """Preserve provider side-channel fields dropped by LangChain conversion."""
        chat_result = super()._create_chat_result(response, generation_info)
        response_dict = response if isinstance(response, dict) else response.model_dump()
        choices = response_dict.get("choices") or []
        for generation, choice in zip(chat_result.generations, choices):
            message_dict = choice.get("message") or {}
            _attach_provider_state_to_message(
                generation.message,
                message_dict,
                self.provider_family,
            )
        return chat_result

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ):
        """Preserve provider reasoning deltas on streaming chunks."""
        generation_chunk = super()._convert_chunk_to_generation_chunk(
            chunk,
            default_chunk_class,
            base_generation_info,
        )
        if generation_chunk is None:
            return None

        choices = chunk.get("choices", []) or chunk.get("chunk", {}).get("choices", [])
        if choices:
            delta = choices[0].get("delta") or {}
            _attach_provider_state_to_message(
                generation_chunk.message,
                delta,
                self.provider_family,
            )
        return generation_chunk

    def _get_request_payload(
        self,
        input_,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        messages = self._convert_input(input_).to_messages()
        if stop is not None:
            kwargs["stop"] = stop
        message_dicts = [
            _convert_message_to_provider_dict(message, self.provider_family)
            for message in messages
        ]
        if self.strict_provider_compat_fallback:
            message_dicts = _sanitize_provider_tool_adjacency(message_dicts)
        _assert_provider_tool_adjacency(message_dicts)
        return {
            "messages": message_dicts,
            **self._default_params,
            **kwargs,
        }


def merge_reasoning_text(*parts: str) -> str:
    """Merge reasoning snippets without duplicating identical segments."""
    merged: list[str] = []
    seen: set[str] = set()
    for part in parts:
        normalized = (part or "").strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            merged.append(normalized)
    return "\n".join(merged)


def _sanitize_provider_tool_adjacency(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop runtime-corrupt tool-call groups before provider submission."""
    sanitized: list[dict[str, Any]] = []
    i = 0
    while i < len(messages):
        message = messages[i]
        if message.get("role") == "assistant" and message.get("tool_calls"):
            required_ids = [
                tool_call.get("id")
                for tool_call in message.get("tool_calls", [])
                if tool_call.get("id")
            ]
            j = i + 1
            tool_messages: list[dict[str, Any]] = []
            tool_ids: list[str | None] = []
            while j < len(messages) and messages[j].get("role") == "tool":
                tool_messages.append(messages[j])
                tool_ids.append(messages[j].get("tool_call_id"))
                j += 1

            valid = (
                len(tool_ids) >= len(required_ids)
                and all(tool_call_id in tool_ids for tool_call_id in required_ids)
                and all(tool_call_id in required_ids for tool_call_id in tool_ids)
            )
            if valid:
                sanitized.append(message)
                sanitized.extend(tool_messages)
            i = max(j, i + 1)
            continue

        if message.get("role") == "tool":
            i += 1
            continue

        sanitized.append(message)
        i += 1

    return sanitized


def _assert_provider_tool_adjacency(messages: list[dict[str, Any]]) -> None:
    issues = _find_provider_tool_adjacency_issues(messages)
    if issues:
        raise TranscriptIntegrityError(issues)


def _find_provider_tool_adjacency_issues(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Find OpenAI-compatible tool-call adjacency violations in payload dicts."""
    issues: list[dict[str, Any]] = []
    i = 0
    while i < len(messages):
        message = messages[i]
        role = message.get("role")
        if role == "assistant" and message.get("tool_calls"):
            required_ids = [
                tool_call.get("id")
                for tool_call in message.get("tool_calls", [])
                if tool_call.get("id")
            ]
            j = i + 1
            tool_indices: list[int] = []
            tool_ids: list[str | None] = []
            while j < len(messages) and messages[j].get("role") == "tool":
                tool_indices.append(j)
                tool_ids.append(messages[j].get("tool_call_id"))
                j += 1

            missing_ids = [
                tool_call_id
                for tool_call_id in required_ids
                if tool_call_id not in tool_ids
            ]
            extra_ids = [
                tool_call_id
                for tool_call_id in tool_ids
                if tool_call_id not in required_ids
            ]
            if (
                len(tool_ids) < len(required_ids)
                or missing_ids
                or extra_ids
            ):
                issues.append({
                    "index": i,
                    "role": "assistant",
                    "tool_call_ids": required_ids,
                    "following_tool_indices": tool_indices,
                    "following_tool_call_ids": tool_ids,
                    "missing_tool_call_ids": missing_ids,
                    "extra_tool_call_ids": extra_ids,
                })
            i = max(j, i + 1)
            continue

        if role == "tool":
            issues.append({
                "index": i,
                "role": "tool",
                "tool_call_id": message.get("tool_call_id"),
                "reason": "orphan_tool_message",
            })

        i += 1

    return issues


def append_provider_state_delta(
    target: dict[str, Any], new_state: dict[str, Any] | None,
) -> dict[str, Any]:
    """Append provider continuation state from streaming deltas."""
    if not new_state:
        return target
    for key, value in new_state.items():
        if key not in target:
            target[key] = value
            continue
        target[key] = _append_provider_value(target[key], value)
    return target


def merge_provider_state_final(
    target: dict[str, Any], new_state: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge final provider state, letting complete message values win."""
    if not new_state:
        return target
    for key, value in new_state.items():
        if value is not None:
            target[key] = value
    return target


def merge_provider_state(target: dict[str, Any], new_state: dict[str, Any] | None) -> dict[str, Any]:
    """Backward-compatible final-state merge helper."""
    return merge_provider_state_final(target, new_state)


def _append_provider_value(existing: Any, incoming: Any) -> Any:
    if incoming is None:
        return existing
    if isinstance(existing, str) and isinstance(incoming, str):
        return existing + incoming
    if isinstance(existing, list) and isinstance(incoming, list):
        return existing + incoming
    if isinstance(existing, dict) and isinstance(incoming, dict):
        merged = dict(existing)
        for key, value in incoming.items():
            if key in merged:
                merged[key] = _append_provider_value(merged[key], value)
            else:
                merged[key] = value
        return merged
    return incoming


def extract_reasoning_from_text(text: str) -> str:
    """Extract visible reasoning from legacy <think> tags in plain text."""
    import re

    if not text:
        return ""
    parts = re.findall(r"<think>([\s\S]*?)</think>", text)
    return "\n".join(p.strip() for p in parts if isinstance(p, str) and p.strip())


def strip_reasoning_tags(text: str) -> str:
    """Strip legacy model XML tags from visible assistant text."""
    import re

    text = re.sub(r"<(\w[\w:_-]*)>[\s\S]*?</\1>", "", text)
    text = re.sub(r"</?(?:think|minimax:\w+)(?:\s[^>]*)?>", "", text)
    return text.strip()


def extract_reasoning_from_message(message: Any) -> ReasoningExtraction:
    """Extract visible reasoning text and provider state from a chunk/message."""
    visible_parts: list[str] = []
    provider_state: dict[str, Any] = {}
    sources: list[str] = []

    content = getattr(message, "content", "")
    content_text = _flatten_content_text(content)
    if content_text:
        text_reasoning = extract_reasoning_from_text(content_text)
        if text_reasoning:
            visible_parts.append(text_reasoning)
            sources.append("think_tag")

    for container_name in ("additional_kwargs", "response_metadata"):
        container = getattr(message, container_name, None)
        if not isinstance(container, dict):
            continue
        extracted = _extract_reasoning_from_mapping(container)
        if extracted.visible_text:
            visible_parts.append(extracted.visible_text)
        if extracted.provider_state:
            merge_provider_state(provider_state, extracted.provider_state)
        if extracted.source:
            sources.append(extracted.source)

    return ReasoningExtraction(
        visible_text=merge_reasoning_text(*visible_parts),
        provider_state=provider_state or None,
        source="|".join(dict.fromkeys(sources)),
    )


def _extract_reasoning_from_mapping(mapping: dict[str, Any]) -> ReasoningExtraction:
    visible_parts: list[str] = []
    provider_state: dict[str, Any] = {}
    sources: list[str] = []

    for key in _REASONING_KEYS:
        if key not in mapping:
            continue
        raw = mapping.get(key)
        text = _flatten_reasoning_text(raw)
        if text:
            visible_parts.append(text)
        provider_state[key] = raw
        sources.append(key)

    delta = mapping.get("delta")
    if isinstance(delta, dict):
        nested = _extract_reasoning_from_mapping(delta)
        if nested.visible_text:
            visible_parts.append(nested.visible_text)
        if nested.provider_state:
            merge_provider_state(provider_state, nested.provider_state)
        if nested.source:
            sources.append(f"delta:{nested.source}")

    return ReasoningExtraction(
        visible_text=merge_reasoning_text(*visible_parts),
        provider_state=provider_state or None,
        source="|".join(dict.fromkeys(sources)),
    )


def _flatten_reasoning_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [_flatten_reasoning_text(item) for item in value]
        return merge_reasoning_text(*parts)
    if isinstance(value, dict):
        for key in ("text", "content"):
            if key in value:
                return _flatten_reasoning_text(value.get(key))
        for key in _REASONING_KEYS:
            if key in value:
                return _flatten_reasoning_text(value.get(key))
        if "delta" in value:
            return _flatten_reasoning_text(value.get("delta"))
    return ""


def _flatten_content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [_flatten_content_text(item) for item in value]
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        for key in ("text", "content"):
            text = _flatten_content_text(value.get(key))
            if text:
                return text
    return ""


def _normalize_reasoning_request_params(
    provider_family: str, extra_params: dict[str, Any],
) -> dict[str, Any]:
    """Map a few common thinking aliases into provider-preferred request shapes."""
    normalized = dict(extra_params)
    extra_body = dict(normalized.get("extra_body", {}) or {})

    if provider_family == "deepseek":
        thinking = normalized.pop("thinking", None)
        enable_thinking = normalized.pop("enable_thinking", None)
        if "thinking" not in extra_body:
            value = thinking if thinking is not None else enable_thinking
            maybe_thinking = _coerce_thinking_config(value)
            if maybe_thinking is not None:
                extra_body["thinking"] = maybe_thinking

    elif provider_family == "qwen":
        thinking = normalized.pop("thinking", None)
        if "enable_thinking" not in extra_body:
            maybe_enable = _coerce_enable_thinking(
                normalized.get("enable_thinking", None) if "enable_thinking" in normalized else thinking,
            )
            if maybe_enable is not None:
                extra_body["enable_thinking"] = maybe_enable
        if isinstance(thinking, dict):
            budget = thinking.get("budget") or thinking.get("thinking_budget")
            if budget is not None and "thinking_budget" not in normalized:
                normalized["thinking_budget"] = budget

    elif provider_family == "glm":
        thinking = normalized.get("thinking")
        if thinking is None and "enable_thinking" in normalized:
            maybe_thinking = _coerce_thinking_config(normalized.pop("enable_thinking"))
            if maybe_thinking is not None:
                normalized["thinking"] = maybe_thinking

    if extra_body:
        normalized["extra_body"] = extra_body
    return normalized


def _coerce_thinking_config(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        if "type" in value:
            return value
        if "enabled" in value:
            return {"type": "enabled" if value["enabled"] else "disabled"}
        return value
    if isinstance(value, bool):
        return {"type": "enabled" if value else "disabled"}
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"enabled", "enable", "true", "on"}:
            return {"type": "enabled"}
        if lowered in {"disabled", "disable", "false", "off"}:
            return {"type": "disabled"}
    return None


def _coerce_enable_thinking(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        thinking_type = value.get("type")
        if isinstance(thinking_type, str):
            return thinking_type.strip().lower() == "enabled"
        if "enabled" in value:
            return bool(value["enabled"])
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"enabled", "enable", "true", "on"}:
            return True
        if lowered in {"disabled", "disable", "false", "off"}:
            return False
    return None


def _convert_message_to_provider_dict(message: BaseMessage, provider_family: str) -> dict[str, Any]:
    """Convert a LangChain message, preserving provider continuation keys when needed."""
    message_dict: dict[str, Any] = {"content": _format_message_content(message.content)}
    if (name := message.name or message.additional_kwargs.get("name")) is not None:
        message_dict["name"] = name

    if isinstance(message, ChatMessage):
        message_dict["role"] = message.role
    elif isinstance(message, HumanMessage):
        message_dict["role"] = "user"
    elif isinstance(message, AIMessage):
        if message.additional_kwargs.get("agentd_internal") == "subtask_result_bridge":
            message_dict["role"] = "user"
            return message_dict
        message_dict["role"] = "assistant"
        if "function_call" in message.additional_kwargs:
            message_dict["function_call"] = message.additional_kwargs["function_call"]
        if message.tool_calls or message.invalid_tool_calls:
            message_dict["tool_calls"] = [
                _lc_tool_call_to_openai_tool_call(tool_call)
                for tool_call in message.tool_calls
            ] + [
                _lc_invalid_tool_call_to_openai_tool_call(tool_call)
                for tool_call in message.invalid_tool_calls
            ]
        elif "tool_calls" in message.additional_kwargs:
            tool_call_supported_props = {"id", "type", "function"}
            message_dict["tool_calls"] = [
                {k: v for k, v in tool_call.items() if k in tool_call_supported_props}
                for tool_call in message.additional_kwargs["tool_calls"]
            ]

        for key in _PROVIDER_CONTINUATION_KEYS.get(provider_family, ()):
            if key in message.additional_kwargs:
                message_dict[key] = message.additional_kwargs[key]

        if "function_call" in message_dict or "tool_calls" in message_dict:
            message_dict["content"] = message_dict["content"] or None
    elif isinstance(message, SystemMessage):
        message_dict["role"] = "system"
    elif isinstance(message, FunctionMessage):
        message_dict["role"] = "function"
    elif isinstance(message, ToolMessage):
        message_dict["role"] = "tool"
        message_dict["tool_call_id"] = message.tool_call_id
        supported_props = {"content", "role", "tool_call_id"}
        message_dict = {k: v for k, v in message_dict.items() if k in supported_props}
    else:
        raise TypeError(f"Got unknown type {message}")

    return message_dict


def _attach_provider_state_to_message(
    message: Any,
    payload: dict[str, Any],
    provider_family: str,
) -> None:
    """Copy provider continuation fields from raw API payload to LangChain messages."""
    keys = _PROVIDER_CONTINUATION_KEYS.get(provider_family, ())
    if not keys or not isinstance(payload, dict):
        return

    additional_kwargs = dict(getattr(message, "additional_kwargs", {}) or {})
    changed = False
    for key in keys:
        if key in payload and payload.get(key) is not None:
            additional_kwargs[key] = payload[key]
            changed = True
    if changed:
        message.additional_kwargs = additional_kwargs
