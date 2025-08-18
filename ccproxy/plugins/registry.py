"""Plugin registry for managing provider plugins."""

import asyncio
from pathlib import Path
from typing import Any

import structlog

from ccproxy.core.services import CoreServices
from ccproxy.plugins.loader import PluginLoader
from ccproxy.plugins.protocol import HealthCheckResult, ProviderPlugin
from ccproxy.scheduler.registry import get_task_registry
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
        self._plugin_tasks: dict[str, list[str]] = {}  # Track tasks per plugin
        self._plugin_paths: dict[
            str, Path
        ] = {}  # Track plugin file paths for efficient reloading

    async def discover_and_initialize(self, services: CoreServices) -> None:
        """Discover and initialize all plugins.

        Args:
            services: Core services to pass to plugins
        """
        self._services = services
        loader = PluginLoader()
        plugins_with_paths = loader.load_plugins_with_paths()

        for plugin, path in plugins_with_paths:
            await self.register_and_initialize(plugin, path)

    async def register_and_initialize(
        self, plugin: ProviderPlugin, plugin_path: Path | None = None
    ) -> None:
        """Register and initialize a plugin.

        Args:
            plugin: Plugin to register and initialize
            plugin_path: Optional path to the plugin file for reloading
        """
        try:
            # Basic validation first
            if not await plugin.validate():
                logger.warning(f"Plugin {plugin.name} failed validation")
                return

            # Register plugin
            self._plugins[plugin.name] = plugin

            # Store plugin path if provided
            if plugin_path:
                self._plugin_paths[plugin.name] = plugin_path

            # Initialize plugin if services available
            if self._services and hasattr(plugin, "initialize"):
                await plugin.initialize(self._services)
                self._initialized_plugins.add(plugin.name)

            # Create adapter after initialization (with proxy service if available)
            adapter = self._create_adapter_with_proxy_service(plugin)
            self._adapters[plugin.name] = adapter

            # Register scheduled tasks if plugin has them and scheduler is available
            await self._register_plugin_tasks(plugin)

            logger.debug(
                f"Registered and initialized plugin: {plugin.name} v{plugin.version}"
            )

        except Exception as e:
            logger.error(f"Failed to register plugin {plugin.name}: {e}")
            # Remove from registry if registration failed
            self._plugins.pop(plugin.name, None)
            self._adapters.pop(plugin.name, None)
            self._initialized_plugins.discard(plugin.name)

    async def _register_plugin_tasks(self, plugin: ProviderPlugin) -> None:
        """Register scheduled tasks for a plugin if available.

        Args:
            plugin: Plugin to register tasks for
        """
        # Check if plugin has scheduled tasks
        if not hasattr(plugin, "get_scheduled_tasks"):
            return

        task_definitions = plugin.get_scheduled_tasks()
        if not task_definitions:
            return

        # Check if scheduler is available
        if not self._services or not self._services.scheduler:
            logger.debug(
                f"Scheduler not available, skipping task registration for {plugin.name}"
            )
            return

        scheduler = self._services.scheduler
        task_registry = get_task_registry()
        registered_tasks = []

        for task_def in task_definitions:
            try:
                # Register task class with task registry if needed
                task_type = task_def["task_type"]
                task_class = task_def["task_class"]

                if not task_registry.is_registered(task_type):
                    task_registry.register(task_type, task_class)
                    logger.debug(f"Registered task type: {task_type}")

                # Add task to scheduler
                task_name = task_def["task_name"]
                interval_seconds = task_def["interval_seconds"]
                enabled = task_def.get("enabled", True)

                # Extract additional kwargs (like detection_service)
                task_kwargs = {
                    k: v
                    for k, v in task_def.items()
                    if k
                    not in [
                        "task_name",
                        "task_type",
                        "task_class",
                        "interval_seconds",
                        "enabled",
                    ]
                }

                await scheduler.add_task(
                    task_name=task_name,
                    task_type=task_type,
                    interval_seconds=interval_seconds,
                    enabled=enabled,
                    **task_kwargs,
                )

                registered_tasks.append(task_name)
                logger.debug(
                    f"Registered scheduled task '{task_name}' for plugin {plugin.name}",
                    interval_seconds=interval_seconds,
                    enabled=enabled,
                )

            except Exception as e:
                logger.error(
                    f"Failed to register task for plugin {plugin.name}: {e}",
                    task_def=task_def,
                )

        # Track tasks for this plugin for cleanup
        if registered_tasks:
            self._plugin_tasks[plugin.name] = registered_tasks

    def _create_adapter_with_proxy_service(self, plugin: ProviderPlugin) -> BaseAdapter:
        """Create adapter with ProxyService reference to avoid set_proxy_service anti-pattern.

        Since plugins now receive ProxyService via CoreServices during initialization,
        adapters should be fully initialized when created. This factory method
        is kept for backward compatibility but should no longer need special handling.

        Args:
            plugin: Plugin instance to create adapter for

        Returns:
            Properly initialized adapter with ProxyService reference
        """
        # The plugin should have created the adapter with all dependencies
        # via the ProxyService reference in CoreServices during initialization
        adapter = plugin.create_adapter()

        # Legacy support: If adapter still has set_proxy_service and wasn't properly initialized
        if (
            hasattr(adapter, "set_proxy_service")
            and self._services
            and hasattr(self._services, "proxy_service")
            and self._services.proxy_service
            and (not hasattr(adapter, "proxy_service") or adapter.proxy_service is None)
        ):
            logger.warning(
                f"Adapter {type(adapter).__name__} still using deprecated set_proxy_service pattern",
                plugin=plugin.name,
            )
            adapter.set_proxy_service(self._services.proxy_service)

        return adapter

    def get_plugin_summary(self, plugin_name: str) -> dict[str, Any] | None:
        """Get consolidated summary information for a plugin.

        Args:
            plugin_name: Name of the plugin

        Returns:
            Dictionary with plugin summary data or None if plugin not found
        """
        plugin = self._plugins.get(plugin_name)
        adapter = self._adapters.get(plugin_name)

        if not plugin or not adapter:
            return None

        # Start with basic plugin info
        summary = {
            "plugin": plugin_name,
            "status": "ready"
            if plugin_name in self._initialized_plugins
            else "not_ready",
        }

        # Let plugin provide its own summary if it has the method
        if hasattr(plugin, "get_summary"):
            plugin_summary = plugin.get_summary()
            if isinstance(plugin_summary, dict):
                summary.update(plugin_summary)

        # Add routes information if plugin has router
        if hasattr(plugin, "router_prefix"):
            routes_list: list[str] = [plugin.router_prefix]
            summary["routes"] = routes_list  # type: ignore[assignment]

        return summary

    async def get_plugin_summary_with_auth(
        self, plugin_name: str
    ) -> dict[str, Any] | None:
        """Get consolidated summary information for a plugin including detailed auth status.

        Args:
            plugin_name: Name of the plugin

        Returns:
            Dictionary with plugin summary data including auth details or None if plugin not found
        """
        plugin = self._plugins.get(plugin_name)
        adapter = self._adapters.get(plugin_name)

        if not plugin or not adapter:
            return None

        # Start with basic plugin info
        summary = {
            "plugin": plugin_name,
            "status": "ready"
            if plugin_name in self._initialized_plugins
            else "not_ready",
        }

        # Let plugin provide its own summary if it has the method
        if hasattr(plugin, "get_summary"):
            plugin_summary = plugin.get_summary()
            if isinstance(plugin_summary, dict):
                summary.update(plugin_summary)

        # Get detailed auth information if plugin supports it
        if hasattr(plugin, "get_auth_summary"):
            try:
                auth_summary = await plugin.get_auth_summary()
                if isinstance(auth_summary, dict):
                    summary.update(auth_summary)
            except Exception as e:
                logger.debug(f"Failed to get auth summary for {plugin_name}: {e}")

        # Add routes information if plugin has router
        if hasattr(plugin, "get_routes"):
            try:
                router = plugin.get_routes()
                if router and hasattr(router, "routes"):
                    routes_list: list[str] = []
                    for route in router.routes:
                        if hasattr(route, "path"):
                            routes_list.append(route.path)
                        elif hasattr(route, "path_regex"):
                            routes_list.append(str(route.path_regex))
                    summary["routes"] = routes_list  # type: ignore[assignment]
            except Exception as e:
                logger.debug(
                    f"Failed to get routes for plugin {plugin_name}: {e}",
                    exc_info=True,
                )

        return summary

    def get_all_registered_tasks(self) -> list[str]:
        """Get list of all registered task names across all plugins."""
        all_tasks = []
        for tasks in self._plugin_tasks.values():
            all_tasks.extend(tasks)
        return all_tasks

    async def shutdown_all(self) -> None:
        """Shutdown all initialized plugins."""
        # Remove plugin tasks from scheduler first
        await self._unregister_all_plugin_tasks()

        shutdown_tasks = []

        for plugin_name in list(self._initialized_plugins):
            plugin = self._plugins.get(plugin_name)
            if plugin and hasattr(plugin, "shutdown"):
                shutdown_tasks.append(self._shutdown_plugin(plugin))

        if shutdown_tasks:
            await asyncio.gather(*shutdown_tasks, return_exceptions=True)

        self._initialized_plugins.clear()

    async def _unregister_all_plugin_tasks(self) -> None:
        """Unregister all plugin tasks from scheduler."""
        if not self._services or not self._services.scheduler:
            return

        scheduler = self._services.scheduler

        for plugin_name, task_names in self._plugin_tasks.items():
            for task_name in task_names:
                try:
                    await scheduler.remove_task(task_name)
                    logger.debug(f"Removed task '{task_name}' for plugin {plugin_name}")
                except Exception as e:
                    logger.error(
                        f"Failed to remove task '{task_name}' for plugin {plugin_name}: {e}"
                    )

        self._plugin_tasks.clear()

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

    async def reload_plugin(self, name: str) -> bool:
        """Reload a specific plugin efficiently.

        Args:
            name: Plugin name

        Returns:
            True if reloaded successfully
        """
        # Get stored path
        plugin_path = self._plugin_paths.get(name)
        if not plugin_path:
            logger.error(f"No path found for plugin {name}")
            return False

        # Unregister old version
        await self.unregister(name)

        # Load just this plugin
        loader = PluginLoader()
        plugin_dir = plugin_path.parent
        plugin = loader.load_single_plugin(plugin_dir)

        if plugin and plugin.name == name:
            await self.register_and_initialize(plugin, plugin_path)
            return True

        # Check if registered
        return name in self._plugins
