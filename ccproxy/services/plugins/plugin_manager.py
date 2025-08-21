"""Plugin lifecycle and adapter registry management."""

from typing import Any
from urllib.parse import urlparse

import httpx

from ccproxy.config.constants import (
    DEFAULT_TASK_INTERVAL,
    PLUGIN_SUMMARY_CACHE_SIZE,
    PLUGIN_SUMMARY_CACHE_TTL,
)
from ccproxy.config.settings import get_settings
from ccproxy.core.logging import get_logger
from ccproxy.core.services import CoreServices
from ccproxy.plugins.loader import PluginLoader
from ccproxy.plugins.registry import PluginRegistry
from ccproxy.services.adapters.base import BaseAdapter
from ccproxy.services.interfaces import IRequestHandler
from ccproxy.services.tracing.interfaces import RequestTracer
from ccproxy.utils.caching import TTLCache


logger = get_logger(__name__)


class PluginManager:
    """Manages plugin lifecycle and adapter registry."""

    def __init__(
        self,
        plugin_registry: PluginRegistry | None = None,
        request_handler: IRequestHandler | None = None,
    ) -> None:
        """Initialize with plugin registry and optional request handler reference.

        - Wraps existing PluginRegistry
        - Maintains adapter cache
        - Tracks initialization state
        - Uses protocol interface for request handler

        Args:
            plugin_registry: Optional plugin registry instance
            request_handler: Optional request handler (following IRequestHandler protocol)
        """
        self.plugin_registry = plugin_registry or PluginRegistry()
        self.adapters: dict[str, BaseAdapter] = {}
        self.tracers: dict[str, RequestTracer] = {}
        self.initialized = False
        self._http_client: httpx.AsyncClient | None = None
        self._request_handler = request_handler  # Store reference using protocol

        # Add cache for plugin summaries (relatively stable data)
        self._plugin_summary_cache = TTLCache(
            maxsize=PLUGIN_SUMMARY_CACHE_SIZE, ttl=PLUGIN_SUMMARY_CACHE_TTL
        )

    async def initialize_plugins(
        self,
        http_client: httpx.AsyncClient,
        proxy_service: IRequestHandler,
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

            # Create proper CoreServices for plugins with registry and proxy service references
            core_services = CoreServices(
                http_client=http_client,
                logger=logger,
                settings=settings,
                scheduler=scheduler,
                plugin_registry=self.plugin_registry,
                proxy_service=proxy_service,  # Pass proxy service reference from parameter
            )

            # Discover all plugins using loader
            loader = PluginLoader()
            plugin_instances = await loader.load_plugins()
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

                    # NOTE: Adapters should already have ProxyService reference from constructor
                    # No need for set_proxy_service() anti-pattern anymore

                    # Store adapter reference locally for quick access
                    if adapter:
                        self.adapters[plugin_instance.name] = adapter

                except ValueError as e:
                    logger.error(
                        "plugin_initialization_validation_failed",
                        plugin=plugin_instance.name,
                        error=str(e),
                        exc_info=e,
                    )
                except AttributeError as e:
                    logger.error(
                        "plugin_initialization_missing_attribute",
                        plugin=plugin_instance.name,
                        error=str(e),
                        exc_info=e,
                    )
                except Exception as e:
                    logger.error(
                        "plugin_initialization_failed",
                        plugin=plugin_instance.name,
                        error=str(e),
                        exc_info=e,
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

            # Log consolidated plugin information with auth details (with caching)
            for plugin_name in self.adapters:
                summary = await self._get_cached_plugin_summary(plugin_name)
                if summary:
                    logger.info("plugin_initialized", **summary)

            # Log consolidated background tasks
            all_tasks = self.plugin_registry.get_all_registered_tasks()
            if all_tasks:
                logger.info(
                    "background_tasks_registered",
                    tasks=len(all_tasks),
                    interval_seconds=DEFAULT_TASK_INTERVAL,
                    names=all_tasks,
                )

            self.initialized = True
            logger.info(
                "plugin_system_ready",
                active_plugins=len(self.adapters),
                total_routes=len(self.adapters),
            )

        except ImportError as e:
            logger.error("plugin_system_import_failed", error=str(e), exc_info=e)
            raise
        except AttributeError as e:
            logger.error("plugin_system_missing_attribute", error=str(e), exc_info=e)
            raise
        except Exception as e:
            logger.error(
                "plugin_system_initialization_failed", error=str(e), exc_info=e
            )
            raise

    def get_adapter(self, name: str) -> BaseAdapter | None:
        """Retrieve plugin adapter by provider name with caching.

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

    def get_tracer(self, provider_name: str) -> RequestTracer | None:
        """Get tracer for a specific provider.

        Returns the request tracer registered for the given provider.
        """
        return self.tracers.get(provider_name)

    def list_providers(self) -> list[str]:
        """Get list of all registered provider names.

        - Returns keys from adapter cache
        - Includes only initialized plugins
        """
        return list(self.adapters.keys())

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
        except (ValueError, TypeError) as e:
            logger.debug("url_parsing_failed", url=url, error=str(e), exc_info=e)
            return False
        except Exception as e:
            logger.debug(
                "unexpected_url_parsing_error", url=url, error=str(e), exc_info=e
            )
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

    def get_plugin_registry(self) -> Any:
        """Get the internal plugin registry for admin operations.

        This method is part of the IPluginRegistry protocol and allows
        admin routes to access the internal registry for management tasks.
        """
        return self.plugin_registry

    def get_adapters_dict(self) -> dict[str, BaseAdapter]:
        """Get the adapters dictionary for admin operations.

        This method is part of the IPluginRegistry protocol and allows
        admin routes to access and modify the adapters dict.
        """
        return self.adapters

    async def close(self) -> None:
        """Clean up plugin resources on shutdown."""
        try:
            # Shutdown plugin registry first (which shutdowns plugins)
            if self.plugin_registry:
                await self.plugin_registry.shutdown_all()

            # Close any plugin-specific resources
            for adapter_name, adapter in list(self.adapters.items()):
                try:
                    # Try cleanup first (preferred method)
                    if hasattr(adapter, "cleanup"):
                        await adapter.cleanup()
                    # Fall back to close for backward compatibility
                    elif hasattr(adapter, "close"):
                        await adapter.close()

                    logger.debug(
                        "adapter_cleaned_up",
                        adapter=type(adapter).__name__,
                        plugin=adapter_name,
                    )

                except Exception as e:
                    logger.error(
                        "adapter_cleanup_failed",
                        adapter=type(adapter).__name__,
                        plugin=adapter_name,
                        error=str(e),
                        exc_info=e,
                    )

            # Clear references
            self.adapters.clear()
            self.tracers.clear()
            self._http_client = None
            self._request_handler = None
            self.initialized = False

            logger.info("plugin_manager_shutdown_completed")

        except Exception as e:
            logger.error(
                "plugin_manager_shutdown_failed",
                error=str(e),
                exc_info=e,
            )

    async def _get_cached_plugin_summary(
        self, plugin_name: str
    ) -> dict[str, Any] | None:
        """Get plugin summary with caching."""
        cache_key = f"plugin_summary:{plugin_name}"

        # Check cache first
        cached_summary = self._plugin_summary_cache.get(cache_key)
        if cached_summary is not None:
            return cached_summary  # type: ignore[no-any-return]

        # Get fresh summary with auth details
        summary = await self.plugin_registry.get_plugin_summary(
            plugin_name, include_auth=True
        )

        # Cache the result (even if None)
        self._plugin_summary_cache.set(cache_key, summary)

        return summary

    def clear_plugin_caches(self) -> None:
        """Clear all plugin-related caches."""
        # No LRU caches to clear anymore (removed to avoid memory leaks)

        # Clear TTL cache
        self._plugin_summary_cache.clear()

        logger.debug("plugin_manager_caches_cleared")

    def invalidate_plugin_cache(self, plugin_name: str) -> None:
        """Invalidate cache for a specific plugin."""
        cache_key = f"plugin_summary:{plugin_name}"
        self._plugin_summary_cache.delete(cache_key)

        # No LRU caches to clear anymore (removed to avoid memory leaks)

        logger.debug("plugin_cache_invalidated", plugin=plugin_name)
