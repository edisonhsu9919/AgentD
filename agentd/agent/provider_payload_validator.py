"""Provider payload validation for AgentD runtime hardening.

Phase v0.4.4 / Phase C: validate the final provider message dicts before
they leave AgentD. This module does not classify LangGraph checkpoints; it
only validates the payload that a provider would receive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class ProviderPayloadIssue:
    code: str
    severity: str = "error"
    index: int | None = None
    role: str | None = None
    tool_call_id: str | None = None
    detail: str | None = None
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = {
            "code": self.code,
            "severity": self.severity,
            "index": self.index,
            "role": self.role,
            "tool_call_id": self.tool_call_id,
            "detail": self.detail,
        }
        data.update(self.context)
        return {key: value for key, value in data.items() if value is not None}


@dataclass(frozen=True)
class ProviderPayloadValidationResult:
    ok: bool
    provider_family: str
    generic_ok: bool
    provider_specific_ok: bool
    issues: list[ProviderPayloadIssue]

    @property
    def issue_dicts(self) -> list[dict[str, Any]]:
        return [issue.to_dict() for issue in self.issues]


class ProviderPayloadValidationError(RuntimeError):
    code = "PROVIDER_PAYLOAD_VALIDATION_ERROR"
    provider_error_category = "provider_payload_validation_error"

    def __init__(
        self,
        result_or_issues: ProviderPayloadValidationResult | list[dict[str, Any]],
        *,
        provider_family: str | None = None,
    ):
        if isinstance(result_or_issues, ProviderPayloadValidationResult):
            self.result = result_or_issues
            self.provider_family = result_or_issues.provider_family
            self.issues = result_or_issues.issue_dicts
        else:
            self.result = None
            self.provider_family = provider_family or "unknown"
            self.issues = list(result_or_issues)
        self.provider_payload_validation_error = True
        super().__init__(
            f"{self.code}: provider payload validation failed "
            f"for {self.provider_family}: {self.issues}"
        )


ProviderSpecificValidator = Callable[
    [list[dict[str, Any]], str | None],
    list[ProviderPayloadIssue],
]


_OPENAI_COMPATIBLE_FAMILIES = {
    "openai",
    "openai_compatible",
    "deepseek",
    "qwen",
    "glm",
    "minimax",
}
_ALLOWED_ROLES = {"system", "user", "assistant", "tool", "function"}


def validate_provider_payload(
    messages: list[dict[str, Any]],
    *,
    provider_family: str,
    model_id: str | None = None,
    strict: bool = True,
) -> ProviderPayloadValidationResult:
    """Validate final provider messages with generic + provider-specific rules."""
    provider_family = _normalize_provider_family(provider_family)
    generic_issues = _validate_generic_payload(messages, strict=strict)
    provider_issues = _validate_provider_specific_payload(
        messages,
        provider_family=provider_family,
        model_id=model_id,
        strict=strict,
    )
    issues = generic_issues + provider_issues
    return ProviderPayloadValidationResult(
        ok=not issues,
        provider_family=provider_family,
        generic_ok=not generic_issues,
        provider_specific_ok=not provider_issues,
        issues=issues,
    )


def assert_provider_payload_valid(
    messages: list[dict[str, Any]],
    *,
    provider_family: str,
    model_id: str | None = None,
    strict: bool = True,
) -> None:
    result = validate_provider_payload(
        messages,
        provider_family=provider_family,
        model_id=model_id,
        strict=strict,
    )
    if not result.ok:
        raise ProviderPayloadValidationError(result)


def sanitize_provider_tool_adjacency(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drop runtime-corrupt tool-call groups before provider submission."""
    sanitized: list[dict[str, Any]] = []
    i = 0
    while i < len(messages):
        message = messages[i]
        if message.get("role") == "assistant" and message.get("tool_calls"):
            required_ids = _assistant_tool_call_ids(message)
            j = i + 1
            tool_messages: list[dict[str, Any]] = []
            tool_ids: list[str | None] = []
            while j < len(messages) and messages[j].get("role") == "tool":
                tool_messages.append(messages[j])
                tool_ids.append(messages[j].get("tool_call_id"))
                j += 1

            valid = (
                len(required_ids) > 0
                and len(tool_ids) >= len(required_ids)
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


def find_provider_tool_adjacency_issues(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Backward-compatible tool adjacency issue finder."""
    return [
        issue.to_dict()
        for issue in _validate_generic_payload(messages, strict=True)
        if issue.code in {
            "assistant_tool_call_missing_tool_result",
            "orphan_tool_message",
            "duplicate_tool_call_id",
            "invalid_tool_call_id",
            "invalid_tool_message_id",
            "unknown_tool_result_id",
        }
    ]


def _validate_generic_payload(
    messages: list[dict[str, Any]],
    *,
    strict: bool,
) -> list[ProviderPayloadIssue]:
    del strict
    issues: list[ProviderPayloadIssue] = []
    i = 0
    while i < len(messages):
        message = messages[i]
        role = message.get("role")
        if role not in _ALLOWED_ROLES:
            issues.append(ProviderPayloadIssue(
                code="unknown_role",
                index=i,
                role=str(role) if role is not None else None,
                detail="provider payload contains an unsupported role",
            ))
            i += 1
            continue

        if role == "assistant" and message.get("tool_calls"):
            tool_calls = message.get("tool_calls") or []
            required_ids: list[str] = []
            invalid_ids: list[Any] = []
            duplicate_ids: list[str] = []
            seen: set[str] = set()
            for tool_call in tool_calls:
                tool_call_id = tool_call.get("id") if isinstance(tool_call, dict) else None
                if not isinstance(tool_call_id, str) or not tool_call_id:
                    invalid_ids.append(tool_call_id)
                    continue
                if tool_call_id in seen:
                    duplicate_ids.append(tool_call_id)
                seen.add(tool_call_id)
                required_ids.append(tool_call_id)

            if invalid_ids:
                issues.append(ProviderPayloadIssue(
                    code="invalid_tool_call_id",
                    index=i,
                    role="assistant",
                    detail="assistant tool_call.id must be a non-empty string",
                    context={
                        "tool_call_ids": required_ids,
                        "invalid_tool_call_ids": invalid_ids,
                    },
                ))
            if duplicate_ids:
                issues.append(ProviderPayloadIssue(
                    code="duplicate_tool_call_id",
                    index=i,
                    role="assistant",
                    tool_call_id=duplicate_ids[0],
                    detail="assistant tool_calls contain duplicate ids",
                    context={
                        "tool_call_ids": required_ids,
                        "duplicate_tool_call_ids": duplicate_ids,
                    },
                ))

            j = i + 1
            tool_indices: list[int] = []
            tool_ids: list[str | None] = []
            invalid_tool_message_ids: list[Any] = []
            while j < len(messages) and messages[j].get("role") == "tool":
                tool_indices.append(j)
                tool_id = messages[j].get("tool_call_id")
                tool_ids.append(tool_id)
                if not isinstance(tool_id, str) or not tool_id:
                    invalid_tool_message_ids.append(tool_id)
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

            if invalid_tool_message_ids:
                issues.append(ProviderPayloadIssue(
                    code="invalid_tool_message_id",
                    index=tool_indices[0] if tool_indices else None,
                    role="tool",
                    detail="tool.tool_call_id must be a non-empty string",
                    context={
                        "tool_call_ids": required_ids,
                        "following_tool_indices": tool_indices,
                        "following_tool_call_ids": tool_ids,
                        "invalid_tool_call_ids": invalid_tool_message_ids,
                    },
                ))
            if len(tool_ids) < len(required_ids) or missing_ids or extra_ids:
                issues.append(ProviderPayloadIssue(
                    code=(
                        "unknown_tool_result_id"
                        if extra_ids and not missing_ids and len(tool_ids) >= len(required_ids)
                        else "assistant_tool_call_missing_tool_result"
                    ),
                    index=i,
                    role="assistant",
                    detail="assistant tool_calls must be followed by matching tool results",
                    context={
                        "tool_call_ids": required_ids,
                        "following_tool_indices": tool_indices,
                        "following_tool_call_ids": tool_ids,
                        "missing_tool_call_ids": missing_ids,
                        "extra_tool_call_ids": extra_ids,
                    },
                ))
            i = max(j, i + 1)
            continue

        if role == "tool":
            tool_id = message.get("tool_call_id")
            issues.append(ProviderPayloadIssue(
                code=(
                    "invalid_tool_message_id"
                    if not isinstance(tool_id, str) or not tool_id
                    else "orphan_tool_message"
                ),
                index=i,
                role="tool",
                tool_call_id=tool_id if isinstance(tool_id, str) else None,
                detail="tool message appears without a preceding assistant tool_call",
                context={"reason": "orphan_tool_message"},
            ))

        i += 1

    return issues


def _validate_provider_specific_payload(
    messages: list[dict[str, Any]],
    *,
    provider_family: str,
    model_id: str | None,
    strict: bool,
) -> list[ProviderPayloadIssue]:
    if not strict:
        return []
    if provider_family in _OPENAI_COMPATIBLE_FAMILIES:
        return _validate_openai_compatible_payload(messages, model_id)
    return []


def _validate_openai_compatible_payload(
    messages: list[dict[str, Any]],
    model_id: str | None,
) -> list[ProviderPayloadIssue]:
    del model_id
    issues: list[ProviderPayloadIssue] = []
    for index, message in enumerate(messages):
        if message.get("role") != "assistant" or not message.get("tool_calls"):
            continue
        for tool_call in message.get("tool_calls") or []:
            if not isinstance(tool_call, dict):
                issues.append(ProviderPayloadIssue(
                    code="invalid_tool_call_shape",
                    index=index,
                    role="assistant",
                    detail="assistant tool_call must be an object",
                ))
                continue
            function = tool_call.get("function")
            if not isinstance(function, dict):
                issues.append(ProviderPayloadIssue(
                    code="invalid_tool_call_function",
                    index=index,
                    role="assistant",
                    tool_call_id=tool_call.get("id"),
                    detail="OpenAI-compatible tool_call.function must be an object",
                ))
                continue
            name = function.get("name")
            if not isinstance(name, str) or not name:
                issues.append(ProviderPayloadIssue(
                    code="missing_tool_call_function_name",
                    index=index,
                    role="assistant",
                    tool_call_id=tool_call.get("id"),
                    detail="OpenAI-compatible tool_call.function.name is required",
                ))
            arguments = function.get("arguments")
            if arguments is not None and not isinstance(arguments, str):
                issues.append(ProviderPayloadIssue(
                    code="invalid_tool_call_function_arguments",
                    index=index,
                    role="assistant",
                    tool_call_id=tool_call.get("id"),
                    detail="OpenAI-compatible tool_call.function.arguments must be a string",
                ))
    return issues


def _assistant_tool_call_ids(message: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for tool_call in message.get("tool_calls") or []:
        if isinstance(tool_call, dict) and isinstance(tool_call.get("id"), str):
            ids.append(tool_call["id"])
    return ids


def _normalize_provider_family(provider_family: str | None) -> str:
    return (provider_family or "openai_compatible").strip().lower() or "openai_compatible"
