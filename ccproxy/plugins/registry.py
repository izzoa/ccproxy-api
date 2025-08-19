"""Plugin registry for managing provider plugins."""

import asyncio
from typing import Any

import structlog

from ccproxy.config.constants import PLUGIN_HEALTH_CHECK_TIMEOUT
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

    async def discover_and_initialize(self, services: CoreServices) -> None:
        """Discover and initialize all plugins.

        Args:
            services: Core services to pass to plugins
        """
        self._services = services
        loader = PluginLoader()
        plugins = await loader.load_plugins()

        for plugin in plugins:
            await self.register_and_initialize(plugin)

    async def register_and_initialize(self, plugin: ProviderPlugin) -> None:
        """Register and initialize a plugin.

        Args:
            plugin: Plugin to register and initialize
        """
        try:
            # Step 1: Validate plugin
            if not await self._validate_plugin(plugin):
                return

            # Step 2: Register plugin
            self._register_plugin(plugin)

            # Step 3: Initialize plugin
            await self._initialize_plugin(plugin)

            # Step 4: Create and register adapter
            self._create_and_register_adapter(plugin)

            # Step 5: Register scheduled tasks
            await self._register_plugin_tasks(plugin)

            logger.debug(
                f"Registered and initialized plugin: {plugin.name} v{plugin.version}"
            )

        except (ValueError, AttributeError, Exception) as e:
            await self._handle_registration_error(plugin, e)

    async def _validate_plugin(self, plugin: ProviderPlugin) -> bool:
        """Validate a plugin before registration.

        Args:
            plugin: Plugin to validate

        Returns:
            True if valid, False otherwise
        """
        # Check if plugin is already registered
        if plugin.name in self._plugins:
            logger.debug(f"Plugin {plugin.name} already registered, skipping duplicate")
            return False

        # Basic validation
        if not await plugin.validate():
            logger.warning(f"Plugin {plugin.name} failed validation")
            return False

        return True

    def _register_plugin(self, plugin: ProviderPlugin) -> None:
        """Register a plugin in the registry.

        Args:
            plugin: Plugin to register
        """
        self._plugins[plugin.name] = plugin

    async def _initialize_plugin(self, plugin: ProviderPlugin) -> None:
        """Initialize a plugin with core services.

        Args:
            plugin: Plugin to initialize
        """
        if self._services and hasattr(plugin, "initialize"):
            await plugin.initialize(self._services)
            self._initialized_plugins.add(plugin.name)

    def _create_and_register_adapter(self, plugin: ProviderPlugin) -> None:
        """Create and register an adapter for a plugin.

        Args:
            plugin: Plugin to create adapter for
        """
        adapter = plugin.create_adapter()
        self._adapters[plugin.name] = adapter

    async def _handle_registration_error(
        self, plugin: ProviderPlugin, error: Exception
    ) -> None:
        """Handle errors during plugin registration.

        Args:
            plugin: Plugin that failed registration
            error: Exception that occurred
        """
        error_type = "plugin_registration_failed"
        if isinstance(error, ValueError):
            error_type = "plugin_registration_validation_failed"
        elif isinstance(error, AttributeError):
            error_type = "plugin_registration_missing_attribute"

        logger.error(
            error_type,
            plugin=plugin.name,
            error=str(error),
            exc_info=error,
        )

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

            except ValueError as e:
                logger.error(
                    "plugin_task_registration_invalid_config",
                    plugin=plugin.name,
                    task_def=task_def,
                    error=str(e),
                    exc_info=e,
                )
            except KeyError as e:
                logger.error(
                    "plugin_task_registration_missing_key",
                    plugin=plugin.name,
                    task_def=task_def,
                    error=str(e),
                    exc_info=e,
                )
            except AttributeError as e:
                logger.error(
                    "plugin_task_registration_missing_attribute",
                    plugin=plugin.name,
                    task_def=task_def,
                    error=str(e),
                    exc_info=e,
                )
            except Exception as e:
                logger.error(
                    "plugin_task_registration_failed",
                    plugin=plugin.name,
                    task_def=task_def,
                    error=str(e),
                    exc_info=e,
                )

        # Track tasks for this plugin for cleanup
        if registered_tasks:
            self._plugin_tasks[plugin.name] = registered_tasks

    async def get_plugin_summary(
        self, plugin_name: str, include_auth: bool = True
    ) -> dict[str, Any] | None:
        """Get consolidated summary information for a plugin.

        Args:
            plugin_name: Name of the plugin
            include_auth: Whether to include detailed auth information (default: True)

        Returns:
            Dictionary with plugin summary data or None if plugin not found
        """
        plugin = self._plugins.get(plugin_name)
        adapter = self._adapters.get(plugin_name)

        if not plugin or not adapter:
            return None

        # Build base summary
        summary = self._build_base_summary(plugin_name, plugin)

        # Add auth details if requested
        if include_auth:
            await self._add_auth_details(summary, plugin, plugin_name)

        # Add routes information
        self._add_routes_info(summary, plugin, plugin_name)

        return summary

    def _build_base_summary(
        self, plugin_name: str, plugin: ProviderPlugin
    ) -> dict[str, Any]:
        """Build base summary for a plugin.

        Args:
            plugin_name: Name of the plugin
            plugin: Plugin instance

        Returns:
            Dictionary with base summary data
        """
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

        return summary

    async def _add_auth_details(
        self, summary: dict[str, Any], plugin: ProviderPlugin, plugin_name: str
    ) -> None:
        """Add auth details to plugin summary.

        Args:
            summary: Summary dictionary to update
            plugin: Plugin instance
            plugin_name: Name of the plugin
        """
        if hasattr(plugin, "get_auth_summary"):
            try:
                auth_summary = await plugin.get_auth_summary()
                if isinstance(auth_summary, dict):
                    summary.update(auth_summary)
            except Exception as e:
                logger.debug(
                    "plugin_auth_summary_failed",
                    plugin=plugin_name,
                    error=str(e),
                    exc_info=e,
                )

    def _add_routes_info(
        self, summary: dict[str, Any], plugin: ProviderPlugin, plugin_name: str
    ) -> None:
        """Add routes information to plugin summary.

        Args:
            summary: Summary dictionary to update
            plugin: Plugin instance
            plugin_name: Name of the plugin
        """
        routes_list: list[str] = []

        # Try to get routes from multiple sources
        if hasattr(plugin, "router_prefix"):
            routes_list.append(plugin.router_prefix)
        elif hasattr(plugin, "get_routes"):
            try:
                router = plugin.get_routes()
                if router and hasattr(router, "routes"):
                    for route in router.routes:
                        if hasattr(route, "path"):
                            routes_list.append(route.path)
                        elif hasattr(route, "path_regex"):
                            routes_list.append(str(route.path_regex))
            except Exception as e:
                logger.debug(
                    "plugin_routes_failed",
                    plugin=plugin_name,
                    error=str(e),
                    exc_info=e,
                )

        if routes_list:
            summary["routes"] = routes_list

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
                except ValueError as e:
                    logger.error(
                        "plugin_task_removal_invalid",
                        plugin=plugin_name,
                        task_name=task_name,
                        error=str(e),
                        exc_info=e,
                    )
                except KeyError as e:
                    logger.error(
                        "plugin_task_removal_not_found",
                        plugin=plugin_name,
                        task_name=task_name,
                        error=str(e),
                        exc_info=e,
                    )
                except Exception as e:
                    logger.error(
                        "plugin_task_removal_failed",
                        plugin=plugin_name,
                        task_name=task_name,
                        error=str(e),
                        exc_info=e,
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
        except AttributeError as e:
            logger.error(
                "plugin_shutdown_missing_method",
                plugin=plugin.name,
                error=str(e),
                exc_info=e,
            )
        except Exception as e:
            logger.error(
                "plugin_shutdown_failed", plugin=plugin.name, error=str(e), exc_info=e
            )

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
                timeout=PLUGIN_HEALTH_CHECK_TIMEOUT,
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
            logger.warning(
                f"Plugin health checks timed out after {PLUGIN_HEALTH_CHECK_TIMEOUT} seconds"
            )
            return {
                "timeout": HealthCheckResult(
                    status="fail",
                    componentId="plugin-health",
                    componentType="system",
                    output=f"Plugin health checks timed out after {PLUGIN_HEALTH_CHECK_TIMEOUT} seconds",
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
