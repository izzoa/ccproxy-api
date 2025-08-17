"""Plugin lifecycle and adapter registry management."""

from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

from ccproxy.config.settings import get_settings
from ccproxy.core.services import CoreServices
from ccproxy.plugins.loader import PluginLoader
from ccproxy.plugins.registry import PluginRegistry
from ccproxy.services.adapters.base import BaseAdapter
from ccproxy.services.tracing.interfaces import RequestTracer


logger = structlog.get_logger(__name__)


class PluginManager:
    """Manages plugin lifecycle and adapter registry."""

    def __init__(self, plugin_registry: PluginRegistry | None = None) -> None:
        """Initialize with plugin registry.

        - Wraps existing PluginRegistry
        - Maintains adapter cache
        - Tracks initialization state
        """
        self.plugin_registry = plugin_registry or PluginRegistry()
        self.adapters: dict[str, BaseAdapter] = {}
        self.tracers: dict[str, RequestTracer] = {}
        self.initialized = False
        self._http_client: httpx.AsyncClient | None = None

    async def initialize_plugins(
        self,
        http_client: httpx.AsyncClient,
        proxy_service: Any,
        scheduler: Any | None = None,
    ) -> None:
        """Discover and initialize all plugins.

        - Creates shared HTTP client
        - Discovers plugins via registry
        - Passes core services to each plugin
        - Registers adapters for routing
        """
        if self.initialized:
            logger.warning("Plugins already initialized")
            return

        try:
            # Store HTTP client reference
            self._http_client = http_client

            # Get settings for CoreServices
            settings = get_settings()

            # Create proper CoreServices for plugins with registry reference
            core_services = CoreServices(
                http_client=http_client,
                logger=logger,
                settings=settings,
                scheduler=scheduler,
                plugin_registry=self.plugin_registry,
            )

            # Discover all plugins using loader
            loader = PluginLoader()
            plugin_instances = await loader.discover_plugins()
            logger.debug(f"Discovered {len(plugin_instances)} plugins")

            # Set services on registry so it can initialize plugins
            self.plugin_registry._services = core_services

            # Register and initialize each plugin using the registry
            for plugin_instance in plugin_instances:
                try:
                    # Use the registry's proper registration method
                    await self.plugin_registry.register_and_initialize(plugin_instance)

                    # Get the adapter from registry
                    adapter = self.plugin_registry._adapters.get(plugin_instance.name)

                    # Set proxy service reference if adapter supports it
                    if adapter and hasattr(adapter, "set_proxy_service"):
                        adapter.set_proxy_service(proxy_service)

                    # Store adapter reference locally for quick access
                    if adapter:
                        self.adapters[plugin_instance.name] = adapter

                except Exception as e:
                    logger.error(
                        "Failed to initialize plugin",
                        plugin=plugin_instance.name,
                        error=str(e),
                    )

            # Log consolidated CLI detection summary
            cli_info = {}
            for plugin_name in self.adapters:
                plugin = self.plugin_registry._plugins.get(plugin_name)
                if (
                    plugin
                    and hasattr(plugin, "_detection_service")
                    and plugin._detection_service
                ):
                    detection = plugin._detection_service
                    if hasattr(detection, "get_cli_path") and hasattr(
                        detection, "get_version"
                    ):
                        cli_path = detection.get_cli_path()
                        cli_version = detection.get_version()
                        if cli_path and cli_version:
                            cli_source = (
                                "package_manager"
                                if isinstance(cli_path, list) and len(cli_path) > 1
                                else "in_path"
                            )
                            cli_info[f"{plugin_name}_version"] = cli_version
                            cli_info[f"{plugin_name}_source"] = cli_source

            if cli_info:
                cli_info["cache_used"] = True  # Most detection uses cache
                logger.info("cli_detection_completed", **cli_info)

            # Log consolidated plugin information with auth details
            for plugin_name in self.adapters:
                summary = await self.plugin_registry.get_plugin_summary_with_auth(
                    plugin_name
                )
                if summary:
                    logger.info("plugin_initialized", **summary)

            # Log consolidated background tasks
            all_tasks = self.plugin_registry.get_all_registered_tasks()
            if all_tasks:
                logger.info(
                    "background_tasks_registered",
                    tasks=len(all_tasks),
                    interval_seconds=3600,  # Standard interval for all detection tasks
                    names=all_tasks,
                )

            self.initialized = True
            logger.info(
                "plugin_system_ready",
                active_plugins=len(self.adapters),
                total_routes=len(self.adapters),
            )

        except Exception as e:
            logger.error("Plugin initialization failed", error=str(e))
            raise

    def get_plugin_adapter(self, name: str) -> BaseAdapter | None:
        """Retrieve plugin adapter by provider name.

        - Looks up in adapter cache
        - Handles scheme-to-name mapping for URL schemes with hyphens
        - Returns None if not found
        - Thread-safe access
        """
        # Direct lookup first
        adapter = self.adapters.get(name)
        if adapter:
            return adapter

        # Handle scheme-to-name mapping (e.g., claude-sdk -> claude_sdk)
        # URL schemes use hyphens but Python identifiers use underscores
        name_with_underscores = name.replace("-", "_")
        return self.adapters.get(name_with_underscores)

    def list_active_providers(self) -> list[str]:
        """Get list of all registered provider names.

        - Returns keys from adapter cache
        - Includes only initialized plugins
        """
        return list(self.adapters.keys())

    def is_plugin_protocol(self, url: str) -> bool:
        """Check if URL uses plugin-specific protocol.

        - Parses URL scheme
        - Returns True for non-http(s) schemes
        - Used for routing decisions
        """
        try:
            parsed = urlparse(url)
            return parsed.scheme not in ("http", "https", "")
        except Exception:
            return False

    async def register_plugin_tracer(
        self, provider_name: str, tracer: RequestTracer
    ) -> None:
        """Register plugin-specific request tracer.

        - Allows plugins to bring own tracer
        - Stores in tracer registry
        - Used during request processing
        """
        self.tracers[provider_name] = tracer
        logger.info(
            "Plugin tracer registered",
            provider=provider_name,
            tracer_type=type(tracer).__name__,
        )

    def get_plugin_tracer(self, provider_name: str) -> RequestTracer | None:
        """Get tracer for specific plugin.

        - Returns plugin's custom tracer if registered
        - Returns None to fall back to core tracer
        """
        return self.tracers.get(provider_name)

    async def close(self) -> None:
        """Clean up plugin resources on shutdown."""
        # Close any plugin-specific resources
        for adapter in self.adapters.values():
            if hasattr(adapter, "close"):
                try:
                    await adapter.close()
                except Exception as e:
                    logger.error(
                        "Error closing adapter",
                        adapter=type(adapter).__name__,
                        error=str(e),
                    )

        self.adapters.clear()
        self.tracers.clear()
        self.initialized = False
