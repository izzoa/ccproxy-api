"""Dependency injection container for all services.

This module provides a clean, testable dependency injection container that
manages service lifecycles and dependencies without singleton anti-patterns.
"""

from typing import TYPE_CHECKING, Any

import httpx
import structlog

from ccproxy.config.settings import Settings
from ccproxy.core.http import BaseProxyClient
from ccproxy.observability.metrics import PrometheusMetrics
from ccproxy.plugins.registry import PluginRegistry
from ccproxy.services.auth import AuthenticationService
from ccproxy.services.config import ProxyConfiguration
from ccproxy.services.credentials.manager import CredentialsManager
from ccproxy.services.factories import ConcreteServiceFactory
from ccproxy.services.http_pool import HTTPPoolManager
from ccproxy.services.mocking import MockResponseHandler
from ccproxy.services.plugins import PluginManager
from ccproxy.services.streaming import StreamingHandler
from ccproxy.services.tracing import CoreRequestTracer


if TYPE_CHECKING:
    from ccproxy.services.proxy_service import ProxyService


logger = structlog.get_logger(__name__)


class ServiceContainer:
    """Dependency injection container for all services.

    This container manages service lifecycles and dependencies using proper
    dependency injection patterns. It removes singleton anti-patterns and
    provides a clean, testable interface for service management.

    Key improvements:
    - No singleton pattern (_instance class variable removed)
    - Uses factory pattern for service creation
    - Implements service interfaces for better testing
    - Manages resource lifecycle properly
    - Supports dependency injection for better testability
    """

    def __init__(self, settings: Settings, service_factory: Any | None = None) -> None:
        """Initialize the service container.

        Args:
            settings: Application settings
            service_factory: Optional service factory (for testing/customization)
        """
        self.settings = settings
        self._factory = service_factory or ConcreteServiceFactory()

        # Service instances (created on-demand, not singletons)
        self._services: dict[str, object] = {}
        self._http_client: httpx.AsyncClient | None = None
        self._pool_manager: HTTPPoolManager | None = None

        logger.debug("ServiceContainer initialized with dependency injection")

    def create_proxy_service(
        self,
        proxy_client: BaseProxyClient,
        credentials_manager: CredentialsManager,
        metrics: PrometheusMetrics | None = None,
    ) -> "ProxyService":
        """Factory method to create fully configured ProxyService.

        Uses dependency inversion with protocols to avoid circular dependencies:
        - ProxyService depends on IPluginRegistry protocol
        - PluginManager implements IPluginRegistry
        - Clean dependency graph with no circles

        Args:
            proxy_client: HTTP proxy client
            credentials_manager: Credentials management service
            metrics: Optional metrics service

        Returns:
            Ready-to-use ProxyService instance with clean dependencies
        """
        # Import here to avoid circular dependency
        from ccproxy.services.proxy_service import ProxyService

        # Create PluginManager first (it doesn't depend on ProxyService directly now)
        plugin_registry = PluginRegistry()
        plugin_manager = PluginManager(
            plugin_registry=plugin_registry,
            request_handler=None,  # Will be set to proxy_service after creation
        )

        # Create ProxyService with PluginManager as IPluginRegistry
        proxy_service = ProxyService(
            proxy_client=proxy_client,
            credentials_manager=credentials_manager,
            settings=self.settings,
            request_tracer=self.get_request_tracer(),
            mock_handler=self.get_mock_handler(),
            streaming_handler=self.get_streaming_handler(metrics),
            auth_service=self.get_auth_service(credentials_manager),
            config=self.get_proxy_config(),
            http_client=self.get_http_client(),
            plugin_registry=plugin_manager,  # PluginManager implements IPluginRegistry
            metrics=metrics,
        )

        logger.debug(
            "ProxyService created with all dependencies (no circular dependencies)"
        )
        return proxy_service

    def get_request_tracer(self) -> CoreRequestTracer:
        """Get request tracer service instance.

        Uses caching to ensure single instance per container lifetime,
        but allows multiple containers for testing.

        Returns:
            Request tracer service instance
        """
        service_key = "request_tracer"
        if service_key not in self._services:
            self._services[service_key] = self._factory.create_request_tracer(
                self.settings
            )
        return self._services[service_key]  # type: ignore

    def get_mock_handler(self) -> MockResponseHandler:
        """Get mock handler service instance.

        Uses caching to ensure single instance per container lifetime,
        but allows multiple containers for testing.

        Returns:
            Mock handler service instance
        """
        service_key = "mock_handler"
        if service_key not in self._services:
            self._services[service_key] = self._factory.create_mock_handler(
                self.settings
            )
        return self._services[service_key]  # type: ignore

    def get_streaming_handler(
        self, metrics: PrometheusMetrics | None = None
    ) -> StreamingHandler:
        """Get streaming handler service instance.

        Uses caching to ensure single instance per container lifetime,
        but allows multiple containers for testing.

        Args:
            metrics: Optional metrics service

        Returns:
            Streaming handler service instance
        """
        service_key = "streaming_handler"
        if service_key not in self._services:
            self._services[service_key] = self._factory.create_streaming_handler(
                self.settings, metrics
            )
        return self._services[service_key]  # type: ignore

    def get_auth_service(
        self, credentials_manager: CredentialsManager
    ) -> AuthenticationService:
        """Get authentication service instance.

        Creates a new instance each time since it depends on credentials_manager
        which may vary between calls.

        Args:
            credentials_manager: Credentials management service

        Returns:
            Authentication service instance
        """
        return self._factory.create_auth_service(credentials_manager)

    def get_proxy_config(self) -> ProxyConfiguration:
        """Get proxy configuration service instance.

        Uses caching to ensure single instance per container lifetime,
        but allows multiple containers for testing.

        Returns:
            Proxy configuration service instance
        """
        service_key = "proxy_config"
        if service_key not in self._services:
            self._services[service_key] = self._factory.create_proxy_config()
        return self._services[service_key]  # type: ignore

    def get_http_client(self) -> httpx.AsyncClient:
        """Get shared HTTP client instance.

        This provides the centralized HTTP client with optimized configuration
        for the proxy use case. Only one HTTP client per container to ensure
        proper resource management.

        Returns:
            Shared httpx.AsyncClient instance
        """
        if not self._http_client:
            # Use pool manager for centralized management
            pool_manager = self.get_pool_manager()
            import asyncio

            self._http_client = asyncio.run(pool_manager.get_shared_client())
        return self._http_client

    def get_pool_manager(self) -> HTTPPoolManager:
        """Get HTTP connection pool manager instance.

        This provides centralized management of HTTP connection pools,
        ensuring efficient resource usage across all components.

        Returns:
            HTTPPoolManager instance
        """
        if not self._pool_manager:
            self._pool_manager = HTTPPoolManager(self.settings)
            logger.debug("Created HTTPPoolManager")
        return self._pool_manager

    async def close(self) -> None:
        """Close all managed resources during shutdown.

        This method properly cleans up all resources managed by the container,
        ensuring graceful shutdown and preventing resource leaks.
        """
        # Close pool manager (which closes all HTTP clients)
        if self._pool_manager:
            await self._pool_manager.close_all()
            self._pool_manager = None
            logger.debug("Closed HTTP pool manager")

        # Close HTTP client if it was created separately
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
            logger.debug("Closed shared HTTP client")

        # Clear service cache
        self._services.clear()
        logger.debug("ServiceContainer resources closed")
