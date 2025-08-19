"""Refactored ProxyService - orchestrates proxy requests using injected services."""

import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import structlog
from fastapi import Request
from starlette.responses import Response, StreamingResponse

from ccproxy.config.settings import Settings
from ccproxy.core.http import BaseProxyClient
from ccproxy.hooks import HookEvent, HookManager
from ccproxy.observability.metrics import PrometheusMetrics
from ccproxy.services.auth import AuthenticationService
from ccproxy.services.cache import ResponseCache
from ccproxy.services.config import ProxyConfiguration
from ccproxy.services.credentials.manager import CredentialsManager
from ccproxy.services.http.connection_pool import ConnectionPoolManager
from ccproxy.services.interfaces import IPluginRegistry
from ccproxy.services.mocking import MockResponseHandler
from ccproxy.services.streaming import StreamingHandler
from ccproxy.services.tracing import CoreRequestTracer


logger = structlog.get_logger(__name__)


class ProxyService:
    """Orchestrates proxy requests using injected services."""

    def __init__(
        self,
        # Core dependencies
        proxy_client: BaseProxyClient,
        credentials_manager: CredentialsManager,
        settings: Settings,
        # Injected services
        request_tracer: CoreRequestTracer,
        mock_handler: MockResponseHandler,
        streaming_handler: StreamingHandler,
        auth_service: AuthenticationService,
        config: ProxyConfiguration,
        http_client: httpx.AsyncClient,  # Shared HTTP client for centralized management
        plugin_registry: IPluginRegistry | None = None,  # Uses protocol interface
        metrics: PrometheusMetrics | None = None,
        response_cache: ResponseCache | None = None,
        connection_pool_manager: ConnectionPoolManager | None = None,
        hook_manager: HookManager | None = None,
    ) -> None:
        """Initialize with all dependencies injected.

        - No service creation inside __init__
        - All dependencies passed from container
        - Stores references only
        """
        # Core dependencies
        self.proxy_client = proxy_client
        self.credentials_manager = credentials_manager
        self.settings = settings

        # Injected services
        self.request_tracer = request_tracer
        self.mock_handler = mock_handler
        self.streaming_handler = streaming_handler
        self.auth_service = auth_service
        self.config = config
        self.plugin_registry = plugin_registry
        self.metrics = metrics

        # Performance optimization services
        self.response_cache = response_cache or ResponseCache()
        self.connection_pool_manager = (
            connection_pool_manager or ConnectionPoolManager()
        )

        # Shared HTTP client (injected for centralized management)
        self.http_client = http_client

        # Hook system
        self.hook_manager = hook_manager

        logger.debug(
            "ProxyService initialized with injected services and performance optimizations"
        )

    def set_plugin_registry(self, plugin_registry: IPluginRegistry) -> None:
        """Set the plugin registry.

        This method allows setting the plugin registry after initialization
        if it wasn't available during construction.
        """
        self.plugin_registry = plugin_registry
        logger.debug("Plugin registry set on ProxyService")

    def set_hook_manager(self, hook_manager: HookManager) -> None:
        """Set the hook manager.

        This method allows setting the hook manager after initialization
        since the hook system is initialized after the proxy service.
        """
        self.hook_manager = hook_manager
        logger.debug("Hook manager set on ProxyService")

    async def handle_request(
        self,
        request: Request,
        endpoint: str,
        method: str,
        provider: str,
        plugin_name: str,
        adapter_handler: Callable[..., Awaitable[Response | StreamingResponse]],
        **kwargs: Any,
    ) -> Response | StreamingResponse:
        """Handle proxy request with hooks.

        This method provides a central point for all provider requests with hook emission.

        Args:
            request: FastAPI request object
            endpoint: Target endpoint path
            method: HTTP method
            provider: Provider name (e.g., 'claude_api', 'codex')
            plugin_name: Plugin name for context
            adapter_handler: The adapter's handle_request method to delegate to
            **kwargs: Additional arguments to pass to adapter

        Returns:
            Response or StreamingResponse from the adapter
        """
        start_time = time.time()

        # Emit request started hook
        if self.hook_manager:
            await self.hook_manager.emit(
                HookEvent.REQUEST_STARTED,
                request=request,
                provider=provider,
                plugin=plugin_name,
                data={"endpoint": endpoint, "method": method},
            )

        try:
            # Delegate to the adapter's handle_request method
            response = await adapter_handler(request, endpoint, method, **kwargs)

            # Calculate duration and extract status
            duration = time.time() - start_time
            status = getattr(response, "status_code", 200)

            # Emit request completed hook
            if self.hook_manager:
                await self.hook_manager.emit(
                    HookEvent.REQUEST_COMPLETED,
                    data={"duration": duration, "status": status},
                    request=request,
                    response=response,
                    provider=provider,
                    plugin=plugin_name,
                )

            return response

        except Exception as e:
            # Emit request failed hook
            if self.hook_manager:
                await self.hook_manager.emit(
                    HookEvent.REQUEST_FAILED,
                    error=e,
                    request=request,
                    provider=provider,
                    plugin=plugin_name,
                    data={"endpoint": endpoint, "method": method},
                )
            raise

    async def initialize_plugins(self, scheduler: Any | None = None) -> None:
        """Initialize plugin system at startup.

        - Delegates to plugin registry
        - Called once during app startup
        - Uses the shared HTTP client for centralized management
        """
        if not self.plugin_registry:
            raise RuntimeError(
                "Plugin registry not set - check ServiceContainer initialization"
            )

        # Initialize plugins with the shared HTTP client
        await self.plugin_registry.initialize_plugins(self.http_client, self, scheduler)

    async def get_pooled_client(
        self, base_url: str | None = None, streaming: bool = False
    ) -> httpx.AsyncClient:
        """Get a pooled HTTP client for the given configuration.

        Args:
            base_url: Base URL for the client
            streaming: Whether to use streaming configuration

        Returns:
            HTTPX AsyncClient from the pool
        """
        if streaming:
            return await self.connection_pool_manager.get_streaming_client(base_url)
        return await self.connection_pool_manager.get_client(base_url)

    async def close(self) -> None:
        """Clean up resources on shutdown.

        - Closes proxy client
        - Closes credentials manager
        - Closes connection pools
        - Does NOT close HTTP client (managed by ServiceContainer)
        """
        try:
            # Close plugin registry if it has a close method
            if self.plugin_registry and hasattr(self.plugin_registry, "close"):
                await self.plugin_registry.close()

            # Close proxy client
            if hasattr(self.proxy_client, "close"):
                await self.proxy_client.close()

            # Close credentials manager
            if hasattr(self.credentials_manager, "close"):
                await self.credentials_manager.close()

            # Close connection pools
            if self.connection_pool_manager:
                await self.connection_pool_manager.close_all()

            # Clear response cache
            if self.response_cache:
                self.response_cache.clear()

            logger.info("ProxyService cleanup complete")

        except (AttributeError, TypeError) as e:
            logger.error("cleanup_attribute_error", error=str(e), exc_info=e)
        except Exception as e:
            logger.error("error_during_cleanup", error=str(e), exc_info=e)
