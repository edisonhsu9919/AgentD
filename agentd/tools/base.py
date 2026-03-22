from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class ToolContext:
    user_id: str
    session_id: str
    user_root: str       # /workspaces/{user_id}/ (permanent user home)
    session_dir: str     # /workspaces/{user_id}/sessions/{session_id}/ (session cwd)
    venv_bin: str        # /workspaces/{user_id}/.venv/bin/
    publish: Callable    # EventBus.publish(session_id, event)


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

    @abstractmethod
    def schema(self) -> dict[str, Any]:
        """Return JSON-schema for tool input parameters."""
        ...

    @abstractmethod
    async def execute(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        """Run the tool and return a result dict."""
        ...
