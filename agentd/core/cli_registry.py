"""CLI Service Registry (Phase 7B).

Loads read-only configuration for allowed external CLI services.
This replaces the concept of an internal LLM model gateway for external systems.
External systems are orchestrated as CLI services that bring their own environment.
"""

import json
import logging
import os
import shlex
from typing import Optional

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

class CliServiceConfig(BaseModel):
    name: str              # e.g., "employee-risk-cli"
    entrypoint: str        # e.g., "/opt/employee-risk-classify/current/bin/employee-risk-cli"
    cwd_policy: str = "session_dir"
    env_kind: str = "isolated"
    supports_detached: bool = False
    owner_skill: str = "*" # Allowed skill
    is_enabled: bool = True

    model_config = ConfigDict(extra="ignore")

class CliRegistry:
    def __init__(self):
        self._services: dict[str, CliServiceConfig] = {}
        self._loaded = False
        # Optional CLI registry path; defaults to a file in /etc or local tests/
        self._registry_path = os.environ.get("AGENTD_CLI_REGISTRY", "/etc/agentd/cli_registry.json")

    def load(self, force: bool = False) -> None:
        if self._loaded and not force:
            return
            
        self._services.clear()
        
        # Test fallback locally
        fallback_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cli_registry.json")
        path_to_load = self._registry_path if os.path.exists(self._registry_path) else fallback_path
        
        if not os.path.exists(path_to_load):
            logger.info("No CLI registry found at %s. CLI service integration disabled.", path_to_load)
            self._loaded = True
            return

        try:
            with open(path_to_load, "r", encoding="utf-8") as f:
                data = json.load(f)
                services = data.get("services", [])
                for item in services:
                    svc = CliServiceConfig(**item)
                    if svc.is_enabled:
                        self._services[svc.name] = svc
            logger.info("Loaded %d active CLI services from %s", len(self._services), path_to_load)
        except Exception as e:
            logger.error("Failed to load CLI registry %s: %s", path_to_load, e)
            
        self._loaded = True

    def get_service(self, name: str) -> Optional[CliServiceConfig]:
        self.load()
        return self._services.get(name)

    def resolve_command(self, raw_command: str) -> tuple[str, Optional[CliServiceConfig]]:
        """
        Check if the raw command starts with a registered service name.
        If yes, replaces the service name with its mapped absolute entrypoint.
        Returns (resolved_command, resolved_service_config).
        """
        self.load()
        if not raw_command or not self._services:
            return raw_command, None
            
        # Extract the first token robustly
        try:
            parts = shlex.split(raw_command)
        except ValueError:
            # Fallback if quotes are malformed
            parts = raw_command.split()
            
        if not parts:
            return raw_command, None
            
        service_name = parts[0]
        svc = self._services.get(service_name)
        if not svc:
            return raw_command, None
            
        # Reconstruct command with the absolute entrypoint to preserve exact arguments spacing
        # This is safer than shlex.join(parts) because it preserves user formatting.
        # Find exactly where service_name ends in the raw string.
        idx = raw_command.find(service_name)
        if idx >= 0:
            tail = raw_command[idx + len(service_name):]
            resolved_cmd = svc.entrypoint + tail
            return resolved_cmd, svc
            
        return raw_command, None

registry = CliRegistry()
