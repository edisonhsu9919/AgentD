"""AgentD application settings (Phase I1 configuration baseline).

Configuration priority (highest → lowest):
  1. Environment variables / .env file
  2. Code defaults below (development fallback only)

For production deployment, ALL required fields must be set via .env or
environment variables.  See .env.example and docs/env/ for templates.
"""

from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Database ────────────────────────────────────────────────────────────
    database_url: str = Field(
        default="postgresql+asyncpg://user:pass@localhost:5432/agentd",
        description=(
            "Async PostgreSQL connection URL. "
            "Format: postgresql+asyncpg://user:password@host:port/dbname"
        ),
    )

    # ── JWT ──────────────────────────────────────────────────────────────────
    secret_key: str = Field(
        default="change-me-in-production",
        description="Secret key for JWT signing. MUST be changed in production.",
    )
    access_token_expire_minutes: int = Field(
        default=60,
        description="Access token lifetime in minutes.",
    )
    refresh_token_expire_days: int = Field(
        default=30,
        description="Refresh token lifetime in days.",
    )

    # ── LLM (OpenAI-compatible API) ─────────────────────────────────────────
    # Supports: local llama.cpp, vLLM, OpenRouter, DeepSeek, MiniMax, etc.
    # After I2 (model config management), DB configuration takes priority;
    # these env vars serve as fallback.
    local_llm_url: str = Field(
        default="http://localhost:8080/v1",
        description="Base URL of the OpenAI-compatible LLM API.",
    )
    llm_api_key: str = Field(
        default="no-key",
        description="API key for the LLM endpoint. Use 'no-key' for local llama.cpp.",
    )
    default_model_id: str = Field(
        default="local-default",
        description=(
            "Default model identifier. Set to the actual model name in your "
            "environment (e.g. 'qwen3-30b', 'MiniMax-M2.5'). "
            "This is an environment fallback; I2 will add DB-level defaults."
        ),
    )
    context_window_tokens: int = Field(
        default=32768,
        description="Context window size in tokens for the default model.",
    )

    # ── VLM (Vision-Language Model, OpenAI-compatible API) ───────────────────
    # Parallel to LLM settings. Empty local_vlm_url means no VLM configured.
    local_vlm_url: str = Field(
        default="",
        description="Base URL of the OpenAI-compatible VLM API. Empty = no VLM.",
    )
    vlm_api_key: str = Field(
        default="",
        description="API key for the VLM endpoint.",
    )
    default_vlm_id: str = Field(
        default="",
        description="Default VLM model identifier (e.g. 'qwen3-vl-flash').",
    )

    # ── Workspace ───────────────────────────────────────────────────────────
    workspace_root: str = Field(
        default="/tmp/agentd/workspaces",
        description=(
            "Parent directory for all user workspaces. "
            "Each user gets a subdirectory: <workspace_root>/<username>/. "
            "The catalog lives at <workspace_root>/_catalog/."
        ),
    )

    # ── Skills (legacy fallback) ────────────────────────────────────────────
    # Runtime skill directory is <user_root>/skills/.
    # Catalog truth is <workspace_root>/_catalog/skills/.
    # This field is a historical fallback and will be removed in a future version.
    skill_dir: str = Field(
        default="/skills",
        description="Legacy skill directory fallback. Not used in normal operation.",
    )

    # ── DB connection pool ──────────────────────────────────────────────────
    # These are per-process settings. If running multiple API workers or
    # background workers, total DB connections = pool_size * num_processes.
    db_pool_size: int = Field(
        default=10,
        description="SQLAlchemy pool_size (per process).",
    )
    db_max_overflow: int = Field(
        default=20,
        description="SQLAlchemy max_overflow (per process).",
    )

    # ── App ──────────────────────────────────────────────────────────────────
    app_title: str = "AgentD"
    app_version: str = "0.3.1"
    debug: str = Field(
        default="",
        description="Debug flag. Any non-empty value enables debug logging (e.g. 'true', 'release').",
    )

    # ── Seed admin ──────────────────────────────────────────────────────────
    # Only used on first startup when the users table is empty.
    # This does NOT mean the admin account is always this user.
    # After initial seed, manage admins through the normal user management API.
    seed_admin_username: Optional[str] = Field(
        default=None,
        description="Username for the initial admin account (first startup only).",
    )
    seed_admin_password: Optional[str] = Field(
        default=None,
        description="Password for the initial admin account (first startup only).",
    )

    @property
    def checkpoint_db_url(self) -> str:
        """Derive a psycopg3-compatible URL from DATABASE_URL for LangGraph checkpointer.

        SQLAlchemy uses ``postgresql+asyncpg://...`` but the checkpoint-postgres
        package expects ``postgresql://...`` (psycopg3 native).
        """
        url = self.database_url
        if "+asyncpg" in url:
            url = url.replace("+asyncpg", "")
        return url


settings = Settings()
