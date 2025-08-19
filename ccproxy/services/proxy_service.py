"""Refactored ProxyService - orchestrates proxy requests using injected services."""

import uuid
from typing import Any

import httpx
import structlog
from fastapi import HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from ccproxy.config.settings import Settings
from ccproxy.core.http import BaseProxyClient
from ccproxy.observability.metrics import PrometheusMetrics
from ccproxy.services.adapters.mock_adapter import MockAdapter
from ccproxy.services.auth import AuthenticationService
from ccproxy.services.config import ProxyConfiguration
from ccproxy.services.credentials.manager import CredentialsManager
from ccproxy.services.handler_config import HandlerConfig
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

        # Shared HTTP client (injected for centralized management)
        self.http_client = http_client

        logger.debug(
            "ProxyService initialized with injected services and shared HTTP client"
        )

    def set_plugin_registry(self, plugin_registry: IPluginRegistry) -> None:
        """Set the plugin registry.

        This method allows setting the plugin registry after initialization
        if it wasn't available during construction.
        """
        self.plugin_registry = plugin_registry
        logger.debug("Plugin registry set on ProxyService")

    async def dispatch_request(
        self,
        request: Request,
        handler_config: HandlerConfig,
        provider_name: str | None = None,
    ) -> Response | StreamingResponse:
        """Pure delegation to adapters.

        DEPRECATED: This method is not used in production. The routing happens
        at the FastAPI level with plugin routers. Kept for test compatibility.

        Args:
            request: The incoming request
            handler_config: The processing configuration
            provider_name: The provider to route to (required since config no longer has it)
        """
        # 1. Check plugin registry is available
        if not self.plugin_registry:
            raise HTTPException(503, "Plugin manager not initialized")

        # 2. Prepare context
        request_id = str(uuid.uuid4())
        body = await request.body()

        # 3. Check bypass mode first
        if self.settings.server.bypass_mode:
            mock_adapter = MockAdapter(self.mock_handler)
            return await mock_adapter.handle_request(
                request, str(request.url.path), request.method, request_id=request_id
            )

        # 4. Get provider adapter - provider_name must be passed explicitly now
        if not provider_name:
            raise HTTPException(400, "Provider name required for dispatch")

        adapter = self.plugin_registry.get_adapter(provider_name)
        if not adapter:
            raise HTTPException(404, f"No adapter for {provider_name}")

        # 5. Adapters should already have ProxyService reference (no set_proxy_service needed)
        # 6. Delegate everything
        return await adapter.handle_request(
            request, str(request.url.path), request.method
        )

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

    async def close(self) -> None:
        """Clean up resources on shutdown.

        - Closes proxy client
        - Closes credentials manager
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

            logger.info("ProxyService cleanup complete")

        except (AttributeError, TypeError) as e:
            logger.error("cleanup_attribute_error", error=str(e), exc_info=e)
        except Exception as e:
            logger.error("error_during_cleanup", error=str(e), exc_info=e)
