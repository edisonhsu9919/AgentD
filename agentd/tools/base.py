from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Literal


@dataclass
class ToolContext:
    user_id: str
    session_id: str
    user_root: str       # /workspaces/{user_id}/ (permanent user home)
    session_dir: str     # /workspaces/{user_id}/sessions/{session_id}/ (session cwd)
    venv_bin: str        # /workspaces/{user_id}/.venv/bin/
    publish: Callable    # EventBus.publish(session_id, event)
    parent_session_dir: str | None = None  # Phase 6: child agent can read parent files




# Result size budget defaults (Phase P4-A)
DEFAULT_MAX_RESULT_SIZE_CHARS = 50_000
MAX_RESULTS_PER_TURN_CHARS = 200_000
# Sentinel: tool uses its own offset/limit semantics (e.g. file_read)
RESULT_SIZE_UNLIMITED = -1


@dataclass(frozen=True)
class ToolMetadata:
    """Unified tool execution semantics (Phase P2 + P4-A).

    Every tool declares its metadata so that registry, permission
    evaluator, diagnostics, compact controller, and future schedulers
    can make decisions from a single source of truth.

    Phase P4-A adds max_result_size_chars: when a tool result exceeds
    this limit, the full output is saved as an artifact and only a
    preview + ref is kept in the conversation.
    """
    default_permission: Literal["allow", "ask"]
    is_read_only: bool
    is_destructive: bool
    is_concurrency_safe: bool
    can_run_in_background: bool
    result_compressibility: Literal["low", "medium", "high"]
    access_scope: Literal[
        "none",
        "session_only",
        "user_scoped",
        "system_scoped",
        "unrestricted",
    ]
    mutates_session_state: bool
    max_result_size_chars: int = DEFAULT_MAX_RESULT_SIZE_CHARS


class BaseTool(ABC):
    """Abstract base for all AgentD tools."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        ...

    @property
    @abstractmethod
    def metadata(self) -> ToolMetadata:
        """Return execution semantics for this tool."""
        ...

    @abstractmethod
    def schema(self) -> dict[str, Any]:
        """Return JSON-schema for tool input parameters."""
        ...

    @abstractmethod
    async def execute(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        """Run the tool and return a result dict."""
        ...
