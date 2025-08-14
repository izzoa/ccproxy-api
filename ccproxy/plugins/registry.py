"""Plugin registry for managing provider plugins."""

import asyncio
from pathlib import Path
from typing import Any

import structlog

from ccproxy.core.services import CoreServices
from ccproxy.plugins.loader import PluginLoader
from ccproxy.plugins.protocol import HealthCheckResult, ProviderPlugin
from ccproxy.services.adapters.base import BaseAdapter


logger = structlog.get_logger(__name__)


class PluginRegistry:
    """Registry for provider plugins with lifecycle management."""

    def __init__(self) -> None:
        """Initialize plugin registry."""
        self._plugins: dict[str, ProviderPlugin] = {}
        self._adapters: dict[str, BaseAdapter] = {}
        self._initialized_plugins: set[str] = set()
        self._services: CoreServices | None = None

    async def discover_and_initialize(self, services: CoreServices) -> None:
        """Discover and initialize all plugins.

        Args:
            services: Core services to pass to plugins
        """
        self._services = services
        loader = PluginLoader()
        plugins = await loader.discover_all_plugins()

        for plugin in plugins:
            await self.register_and_initialize(plugin)

    async def register_and_initialize(self, plugin: ProviderPlugin) -> None:
        """Register and initialize a plugin.

        Args:
            plugin: Plugin to register and initialize
        """
        try:
            # Basic validation first
            if not await plugin.validate():
                logger.warning(f"Plugin {plugin.name} failed validation")
                return

            # Register plugin
            self._plugins[plugin.name] = plugin

            # Initialize plugin if services available
            if self._services and hasattr(plugin, "initialize"):
                await plugin.initialize(self._services)
                self._initialized_plugins.add(plugin.name)

            # Create adapter after initialization
            adapter = plugin.create_adapter()
            self._adapters[plugin.name] = adapter

            logger.info(
                f"Registered and initialized plugin: {plugin.name} v{plugin.version}"
            )

        except Exception as e:
            logger.error(f"Failed to register plugin {plugin.name}: {e}")
            # Remove from registry if registration failed
            self._plugins.pop(plugin.name, None)
            self._adapters.pop(plugin.name, None)
            self._initialized_plugins.discard(plugin.name)

    async def shutdown_all(self) -> None:
        """Shutdown all initialized plugins."""
        shutdown_tasks = []

        for plugin_name in list(self._initialized_plugins):
            plugin = self._plugins.get(plugin_name)
            if plugin and hasattr(plugin, "shutdown"):
                shutdown_tasks.append(self._shutdown_plugin(plugin))

        if shutdown_tasks:
            await asyncio.gather(*shutdown_tasks, return_exceptions=True)

        self._initialized_plugins.clear()

    async def _shutdown_plugin(self, plugin: ProviderPlugin) -> None:
        """Shutdown a single plugin.

        Args:
            plugin: Plugin to shutdown
        """
        try:
            await plugin.shutdown()
            logger.info(f"Shutdown plugin: {plugin.name}")
        except Exception as e:
            logger.error(f"Error shutting down plugin {plugin.name}: {e}")

    async def get_all_health_checks(self) -> dict[str, HealthCheckResult]:
        """Get health checks from all plugins concurrently.

        Returns:
            dict: Plugin health check results by plugin name
        """
        health_tasks = []
        plugin_names = []

        for plugin in self._plugins.values():
            if hasattr(plugin, "health_check"):
                health_tasks.append(plugin.health_check())
                plugin_names.append(plugin.name)

        if not health_tasks:
            return {}

        try:
            results = await asyncio.wait_for(
                asyncio.gather(*health_tasks, return_exceptions=True),
                timeout=10.0,  # 10 second total timeout
            )

            health_results = {}
            for name, result in zip(plugin_names, results, strict=False):
                if isinstance(result, Exception):
                    health_results[name] = HealthCheckResult(
                        status="fail",
                        componentId=f"plugin-{name}",
                        componentType="provider_plugin",
                        output=f"Health check failed: {str(result)}",
                    )
                elif isinstance(result, HealthCheckResult):
                    health_results[name] = result

            return health_results

        except TimeoutError:
            logger.warning("Plugin health checks timed out after 10 seconds")
            return {
                "timeout": HealthCheckResult(
                    status="fail",
                    componentId="plugin-health",
                    componentType="system",
                    output="Plugin health checks timed out after 10 seconds",
                )
            }

    def get_adapter(self, name: str) -> BaseAdapter | None:
        """Get adapter for provider.

        Args:
            name: Provider name

        Returns:
            Adapter instance or None
        """
        return self._adapters.get(name)

    def get_plugin(self, name: str) -> ProviderPlugin | None:
        """Get plugin by name.

        Args:
            name: Plugin name

        Returns:
            Plugin instance or None
        """
        return self._plugins.get(name)

    def get_all_plugins(self) -> list[ProviderPlugin]:
        """Get all registered plugins.

        Returns:
            list: All registered plugin instances
        """
        return list(self._plugins.values())

    def list_plugins(self) -> list[str]:
        """List registered plugins.

        Returns:
            List of plugin names
        """
        return list(self._plugins.keys())

    def list_adapters(self) -> list[str]:
        """List registered adapters.

        Returns:
            List of adapter names
        """
        return list(self._adapters.keys())

    async def unregister(self, name: str) -> bool:
        """Unregister a plugin.

        Args:
            name: Plugin name

        Returns:
            True if unregistered, False if not found
        """
        if name in self._plugins:
            del self._plugins[name]
            if name in self._adapters:
                del self._adapters[name]
            logger.info(f"Unregistered plugin: {name}")
            return True
        return False

    async def reload_plugin(self, name: str, path: Path) -> bool:
        """Reload a specific plugin.

        Args:
            name: Plugin name
            path: Path to plugin file

        Returns:
            True if reloaded successfully
        """
        # Unregister existing
        await self.unregister(name)

        # Use the loader to reload the plugin
        loader = PluginLoader()
        plugins = loader._load_from_directory()
        for plugin in plugins:
            if plugin.name == name:
                await self.register_and_initialize(plugin)
                break

        # Check if registered
        return name in self._plugins
