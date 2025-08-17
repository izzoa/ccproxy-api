"""Core services container for shared services passed to plugins."""

from typing import TYPE_CHECKING, Any

import structlog
from httpx import AsyncClient

from ccproxy.config.settings import Settings


if TYPE_CHECKING:
    from ccproxy.plugins.registry import PluginRegistry
    from ccproxy.scheduler.core import Scheduler


class CoreServices:
    """Container for shared services passed to plugins."""

    def __init__(
        self,
        http_client: AsyncClient,
        logger: structlog.BoundLogger,
        settings: Settings,
        scheduler: "Scheduler | None" = None,
        plugin_registry: "PluginRegistry | None" = None,
    ):
        """Initialize core services.

        Args:
            http_client: Shared HTTP client for plugins
            logger: Shared logger instance
            settings: Application settings
            scheduler: Optional scheduler for plugin tasks
            plugin_registry: Optional plugin registry for config introspection
        """
        self.http_client = http_client
        self.logger = logger
        self.settings = settings
        self.scheduler = scheduler
        self.plugin_registry = plugin_registry

    def get_plugin_config(self, plugin_name: str) -> dict[str, Any]:
        """Get configuration for a specific plugin.

        Args:
            plugin_name: Name of the plugin

        Returns:
            dict: Plugin-specific configuration or empty dict
        """
        # Try to get config from plugin's config class if registry is available
        if self.plugin_registry:
            plugin = self.plugin_registry.get_plugin(plugin_name)
            if plugin and hasattr(plugin, "get_config_class"):
                config_class = plugin.get_config_class()
                if config_class:
                    # Get raw config from settings.plugins dictionary
                    raw_config = self.settings.plugins.get(plugin_name, {})

                    # Validate and return config using plugin's schema
                    try:
                        validated_config = config_class(**raw_config)
                        return validated_config.model_dump()
                    except Exception as e:
                        self.logger.error(f"Invalid config for {plugin_name}: {e}")
                        return {}

        # Fallback to legacy hardcoded configs for backward compatibility
        # This will be removed once all plugins are migrated
        if plugin_name == "claude_sdk":
            # Check if we have it in plugins dict first
            if plugin_name in self.settings.plugins:
                return self.settings.plugins[plugin_name]
            # Otherwise fallback to legacy claude settings
            if hasattr(self.settings, "claude"):
                return self.settings.claude.model_dump()
        elif plugin_name == "claude_api":
            # Check plugins dictionary
            return self.settings.plugins.get(plugin_name, {})
        elif plugin_name == "codex":
            # Codex config now comes from plugins dictionary
            return self.settings.plugins.get("codex", {})

        # Default: look in plugins dictionary
        return self.settings.plugins.get(plugin_name, {})
