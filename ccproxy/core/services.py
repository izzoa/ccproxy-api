"""Core services container for shared services passed to plugins."""

from typing import TYPE_CHECKING, Any

import structlog
from httpx import AsyncClient

from ccproxy.config.settings import Settings

if TYPE_CHECKING:
    from ccproxy.scheduler.core import Scheduler


class CoreServices:
    """Container for shared services passed to plugins."""

    def __init__(
        self,
        http_client: AsyncClient,
        logger: structlog.BoundLogger,
        settings: Settings,
        scheduler: "Scheduler | None" = None,
    ):
        """Initialize core services.

        Args:
            http_client: Shared HTTP client for plugins
            logger: Shared logger instance
            settings: Application settings
            scheduler: Optional scheduler for plugin tasks
        """
        self.http_client = http_client
        self.logger = logger
        self.settings = settings
        self.scheduler = scheduler

    def get_plugin_config(self, plugin_name: str) -> dict[str, Any]:
        """Get configuration for a specific plugin.

        Args:
            plugin_name: Name of the plugin

        Returns:
            dict: Plugin-specific configuration or empty dict
        """
        # Return provider-specific configs based on plugin name
        if plugin_name == "claude_sdk":
            return self.settings.claude.model_dump()
        elif plugin_name == "codex":
            return self.settings.codex.model_dump()
        elif plugin_name == "openai":
            return {}
        else:
            return {}
