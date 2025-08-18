"""Dependency injection container for all services."""

from typing import TYPE_CHECKING

import httpx
import structlog

from ccproxy.adapters.openai.adapter import OpenAIAdapter
from ccproxy.config.settings import Settings
from ccproxy.core.http import BaseProxyClient
from ccproxy.core.http_client import HTTPClientFactory
from ccproxy.observability.metrics import PrometheusMetrics
from ccproxy.plugins.registry import PluginRegistry
from ccproxy.services.auth import AuthenticationService
from ccproxy.services.config import ProxyConfiguration
from ccproxy.services.credentials.manager import CredentialsManager
from ccproxy.services.mocking import MockResponseHandler
from ccproxy.services.plugins import PluginManager
from ccproxy.services.streaming import StreamingHandler
from ccproxy.services.tracing import CoreRequestTracer
from ccproxy.testing import RealisticMockResponseGenerator


if TYPE_CHECKING:
    from ccproxy.services.proxy_service import ProxyService


logger = structlog.get_logger(__name__)


class ServiceContainer:
    """Dependency injection container for all services."""

    def __init__(self, settings: Settings) -> None:
        """Initialize all services with configuration.

        - Creates each service with its dependencies
        - Wires services together
        - Single source of truth for service creation
        """
        self.settings = settings

        # Initialize core services
        self._request_tracer: CoreRequestTracer | None = None
        self._mock_handler: MockResponseHandler | None = None
        self._streaming_handler: StreamingHandler | None = None
        self._auth_service: AuthenticationService | None = None
        self._proxy_config: ProxyConfiguration | None = None
        self._plugin_manager: PluginManager | None = None
        self._metrics: PrometheusMetrics | None = None
        self._http_client: httpx.AsyncClient | None = None

        logger.debug("ServiceContainer initialized")

    def create_proxy_service(
        self,
        proxy_client: BaseProxyClient,
        credentials_manager: CredentialsManager,
        metrics: PrometheusMetrics | None = None,
    ) -> "ProxyService":
        """Factory method to create fully configured ProxyService.

        This method breaks the circular dependency by:
        1. Creating ProxyService without PluginManager first
        2. Creating PluginManager with ProxyService reference
        3. Setting the PluginManager on ProxyService

        - Returns ready-to-use ProxyService instance with no circular dependencies
        """
        # Import here to avoid circular dependency
        from ccproxy.services.proxy_service import ProxyService

        # Store metrics if provided
        if metrics:
            self._metrics = metrics

        # STEP 1: Create ProxyService without PluginManager first
        proxy_service = ProxyService(
            proxy_client=proxy_client,
            credentials_manager=credentials_manager,
            settings=self.settings,
            request_tracer=self.get_request_tracer(),
            mock_handler=self.get_mock_handler(),
            streaming_handler=self.get_streaming_handler(),
            auth_service=self.get_auth_service(credentials_manager),
            config=self.get_proxy_config(),
            http_client=self.get_http_client(),
            plugin_manager=None,  # Will be set in step 3
            metrics=metrics,
        )

        # STEP 2: Create PluginManager with ProxyService reference
        plugin_registry = PluginRegistry()
        plugin_manager = PluginManager(plugin_registry, proxy_service)

        # STEP 3: Set PluginManager on ProxyService
        proxy_service.set_plugin_manager(plugin_manager)

        logger.debug(
            "ProxyService created with all dependencies (no circular dependencies)"
        )
        return proxy_service

    def get_request_tracer(self) -> CoreRequestTracer:
        """Get singleton request tracer instance."""
        if not self._request_tracer:
            self._request_tracer = CoreRequestTracer(
                verbose_api=self.settings.server.verbose_api,
                request_log_dir=self.settings.server.request_log_dir,
            )
            logger.debug("Created CoreRequestTracer")
        return self._request_tracer

    def get_mock_handler(self) -> MockResponseHandler:
        """Get singleton mock handler instance."""
        if not self._mock_handler:
            mock_generator = RealisticMockResponseGenerator()
            openai_adapter = OpenAIAdapter()

            self._mock_handler = MockResponseHandler(
                mock_generator=mock_generator,
                openai_adapter=openai_adapter,
                error_rate=0.05,
                latency_range=(0.5, 2.0),
            )
            logger.debug("Created MockResponseHandler")
        return self._mock_handler

    def get_streaming_handler(self) -> StreamingHandler:
        """Get singleton streaming handler instance."""
        if not self._streaming_handler:
            self._streaming_handler = StreamingHandler(
                metrics=self._metrics,
                verbose_streaming=self.settings.server.verbose_api,
            )
            logger.debug("Created StreamingHandler")
        return self._streaming_handler

    def get_plugin_manager(self) -> PluginManager:
        """Get singleton plugin manager instance.

        NOTE: This method is deprecated in favor of the factory pattern
        in create_proxy_service() which avoids circular dependencies.
        """
        if not self._plugin_manager:
            plugin_registry = PluginRegistry()
            self._plugin_manager = PluginManager(plugin_registry, None)
            logger.debug("Created PluginManager (deprecated path)")
        return self._plugin_manager

    def get_auth_service(
        self, credentials_manager: CredentialsManager
    ) -> AuthenticationService:
        """Get singleton authentication service instance."""
        if not self._auth_service:
            self._auth_service = AuthenticationService(credentials_manager)
            logger.debug("Created AuthenticationService")
        return self._auth_service

    def get_proxy_config(self) -> ProxyConfiguration:
        """Get singleton proxy configuration instance."""
        if not self._proxy_config:
            self._proxy_config = ProxyConfiguration()
            logger.debug("Created ProxyConfiguration")
        return self._proxy_config

    def get_http_client(self) -> httpx.AsyncClient:
        """Get singleton shared HTTP client instance.

        This provides the centralized HTTP client with optimized configuration
        for the proxy use case, addressing Issue #9.

        Returns:
            Shared httpx.AsyncClient instance
        """
        if not self._http_client:
            self._http_client = HTTPClientFactory.create_shared_client(self.settings)
            logger.debug("Created shared HTTP client")
        return self._http_client

    async def close(self) -> None:
        """Close all managed resources during shutdown."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
            logger.debug("Closed shared HTTP client")
