"""Unified runtime environment resolution for executable tools."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from tools.base import ToolContext


@dataclass(frozen=True)
class ExecutionContext:
    """Resolved execution boundary for a single tool invocation."""

    env_kind: Literal["user", "skill", "service"]
    workdir: str
    env_bin: str
    python_bin: str
    path_prefix: str
    skill_name: str = ""
    skill_version: str = ""
    service_name: str = ""

    def as_metadata(self) -> dict[str, str]:
        return {
            "effective_env_kind": self.env_kind,
            "effective_workdir": self.workdir,
            "effective_env_bin": self.env_bin,
            "effective_python_bin": self.python_bin,
            "effective_path_prefix": self.path_prefix,
            "skill_name": self.skill_name,
            "skill_version": self.skill_version,
            "service_name": self.service_name,
        }

    def build_process_env(self, base_env: dict[str, str] | None = None) -> dict[str, str]:
        env = dict(base_env or os.environ)
        if self.path_prefix:
            current_path = env.get("PATH", "")
            env["PATH"] = (
                f"{self.path_prefix}:{current_path}"
                if current_path else self.path_prefix
            )
        env["AGENTD_ENV_KIND"] = self.env_kind
        env["AGENTD_EFFECTIVE_WORKDIR"] = self.workdir
        if self.env_bin:
            env["AGENTD_ENV_BIN"] = self.env_bin
        if self.python_bin:
            env["AGENTD_PYTHON_BIN"] = self.python_bin
        if self.skill_name:
            env["AGENTD_SKILL_NAME"] = self.skill_name
        if self.skill_version:
            env["AGENTD_SKILL_VERSION"] = self.skill_version
        if self.service_name:
            env["AGENTD_SERVICE_NAME"] = self.service_name
        return env


def resolve_command_execution(
    ctx: ToolContext,
    command: str,
    *,
    service=None,
    workdir: str | None = None,
) -> ExecutionContext:
    """Resolve execution boundary for shell-style commands."""
    effective_workdir = workdir or ctx.workspace_dir

    if service and service.env_kind == "isolated":
        return ExecutionContext(
            env_kind="service",
            workdir=effective_workdir,
            env_bin="",
            python_bin="",
            path_prefix="",
            service_name=service.name,
        )

    skill_entry = _find_skill_entry_for_command(ctx.workspace_dir, command)
    if skill_entry:
        env_bin = skill_entry["env_bin"]
        return ExecutionContext(
            env_kind="skill",
            workdir=effective_workdir,
            env_bin=env_bin,
            python_bin=os.path.join(env_bin, "python"),
            path_prefix=env_bin,
            skill_name=skill_entry.get("skill_name", ""),
            skill_version=skill_entry.get("skill_version", ""),
        )

    env_bin = ctx.venv_bin
    return ExecutionContext(
        env_kind="user",
        workdir=effective_workdir,
        env_bin=env_bin,
        python_bin=os.path.join(env_bin, "python") if env_bin else "",
        path_prefix=env_bin,
    )


def resolve_script_execution(ctx: ToolContext, filename: str) -> ExecutionContext:
    """Resolve execution boundary for the script tool."""
    basename = os.path.basename(filename)
    for candidate in (f"scripts/{basename}", basename):
        skill_entry = _find_skill_entry_for_script(ctx.workspace_dir, candidate)
        if skill_entry:
            env_bin = skill_entry["env_bin"]
            return ExecutionContext(
                env_kind="skill",
                workdir=ctx.workspace_dir,
                env_bin=env_bin,
                python_bin=os.path.join(env_bin, "python"),
                path_prefix=env_bin,
                skill_name=skill_entry.get("skill_name", ""),
                skill_version=skill_entry.get("skill_version", ""),
            )

    env_bin = ctx.venv_bin
    return ExecutionContext(
        env_kind="user",
        workdir=ctx.workspace_dir,
        env_bin=env_bin,
        python_bin=os.path.join(env_bin, "python") if env_bin else "",
        path_prefix=env_bin,
    )


def _find_skill_entry_for_script(session_dir: str, script_rel_path: str) -> dict | None:
    from skills.env import read_skill_envs

    entries = read_skill_envs(session_dir).get("entries", {})
    normalized = _normalize_rel_path(script_rel_path)
    entry = entries.get(normalized)
    if entry and _entry_env_exists(entry):
        return entry
    return None


def _find_skill_entry_for_command(session_dir: str, command: str) -> dict | None:
    from skills.env import read_skill_envs

    entries = read_skill_envs(session_dir).get("entries", {})
    for script_path, entry in entries.items():
        if script_path in command and _entry_env_exists(entry):
            return entry
    return None


def _entry_env_exists(entry: dict) -> bool:
    env_bin = entry.get("env_bin", "")
    return bool(env_bin and os.path.isdir(env_bin))


def _normalize_rel_path(rel_path: str) -> str:
    cleaned = os.path.normpath(rel_path)
    if cleaned.startswith("." + os.sep):
        cleaned = cleaned[2:]
    return cleaned
