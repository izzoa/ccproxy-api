"""Dependency injection container for all services.

This module provides a clean, testable dependency injection container that
manages service lifecycles and dependencies without singleton anti-patterns.
"""

from typing import TYPE_CHECKING

import httpx
import structlog

from ccproxy.config.settings import Settings
from ccproxy.core.http import BaseProxyClient
from ccproxy.observability.metrics import PrometheusMetrics
from ccproxy.plugins.registry import PluginRegistry
from ccproxy.services.credentials.manager import CredentialsManager
from ccproxy.services.factories import ConcreteServiceFactory
from ccproxy.services.interfaces import (
    ServiceContainer as ServiceContainerInterface,
    ServiceFactory,
)
from ccproxy.services.auth import AuthenticationService
from ccproxy.services.config import ProxyConfiguration
from ccproxy.services.mocking import MockResponseHandler
from ccproxy.services.streaming import StreamingHandler
from ccproxy.services.tracing import CoreRequestTracer
from ccproxy.services.plugins import PluginManager

if TYPE_CHECKING:
    from ccproxy.services.proxy_service import ProxyService


logger = structlog.get_logger(__name__)


class ServiceContainer(ServiceContainerInterface):
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

    def __init__(
        self, 
        settings: Settings, 
        service_factory: ServiceFactory | None = None
    ) -> None:
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

        logger.debug("ServiceContainer initialized with dependency injection")

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

        Args:
            proxy_client: HTTP proxy client
            credentials_manager: Credentials management service
            metrics: Optional metrics service

        Returns:
            Ready-to-use ProxyService instance with no circular dependencies
        """
        # Import here to avoid circular dependency
        from ccproxy.services.proxy_service import ProxyService

        # STEP 1: Create ProxyService without PluginManager first
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
        """Get request tracer service instance.
        
        Uses caching to ensure single instance per container lifetime,
        but allows multiple containers for testing.
        
        Returns:
            Request tracer service instance
        """
        service_key = "request_tracer"
        if service_key not in self._services:
            self._services[service_key] = self._factory.create_request_tracer(self.settings)
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
            self._services[service_key] = self._factory.create_mock_handler(self.settings)
        return self._services[service_key]  # type: ignore

    def get_streaming_handler(self, metrics: PrometheusMetrics | None = None) -> StreamingHandler:
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
            self._services[service_key] = self._factory.create_streaming_handler(self.settings, metrics)
        return self._services[service_key]  # type: ignore

    def get_auth_service(self, credentials_manager: CredentialsManager) -> AuthenticationService:
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
            self._http_client = self._factory.create_http_client(self.settings)
        return self._http_client

    async def close(self) -> None:
        """Close all managed resources during shutdown.
        
        This method properly cleans up all resources managed by the container,
        ensuring graceful shutdown and preventing resource leaks.
        """
        # Close HTTP client
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
            logger.debug("Closed shared HTTP client")
        
        # Clear service cache
        self._services.clear()
        logger.debug("ServiceContainer resources closed")
