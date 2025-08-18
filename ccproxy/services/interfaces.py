"""Service interfaces for dependency injection container.

This module defines Protocol interfaces for all services managed by the
ServiceContainer, enabling proper type checking, mocking, and testing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Protocol

import httpx


if TYPE_CHECKING:
    from ccproxy.config.settings import Settings
    from ccproxy.observability.metrics import PrometheusMetrics
    from ccproxy.services.credentials.manager import CredentialsManager
    from ccproxy.services.proxy_service import ProxyService


# For now, use type aliases to the concrete classes since ProxyService
# expects the actual implementations. This maintains type safety while
# allowing the container to be testable through dependency injection.

# Type aliases for service interfaces (concrete implementations)
RequestTracerProtocol = "CoreRequestTracer"
MockHandlerProtocol = "MockResponseHandler"
StreamingHandlerProtocol = "StreamingHandler"
AuthServiceProtocol = "AuthenticationService"
ProxyConfigProtocol = "ProxyConfiguration"
PluginManagerProtocol = "PluginManager"


class ServiceFactory(Protocol):
    """Interface for service factories."""

    def create_request_tracer(self, settings: Settings) -> Any:
        """Create request tracer instance."""
        ...

    def create_mock_handler(self, settings: Settings) -> Any:
        """Create mock handler instance."""
        ...

    def create_streaming_handler(
        self, settings: Settings, metrics: PrometheusMetrics | None = None
    ) -> Any:
        """Create streaming handler instance."""
        ...

    def create_auth_service(self, credentials_manager: CredentialsManager) -> Any:
        """Create authentication service instance."""
        ...

    def create_proxy_config(self) -> Any:
        """Create proxy configuration instance."""
        ...

    def create_http_client(self, settings: Settings) -> httpx.AsyncClient:
        """Create HTTP client instance."""
        ...


class ServiceContainer(ABC):
    """Abstract base class for service dependency injection containers.

    This interface defines the contract for service containers that manage
    the lifecycle and dependencies of all services in the application.
    """

    @abstractmethod
    def create_proxy_service(
        self,
        proxy_client: Any,
        credentials_manager: CredentialsManager,
        metrics: PrometheusMetrics | None = None,
    ) -> ProxyService:
        """Create fully configured ProxyService instance.

        Args:
            proxy_client: HTTP proxy client
            credentials_manager: Credentials management service
            metrics: Optional metrics service

        Returns:
            Configured ProxyService instance
        """
        ...

    @abstractmethod
    def get_request_tracer(self) -> Any:
        """Get request tracer service."""
        ...

    @abstractmethod
    def get_mock_handler(self) -> Any:
        """Get mock handler service."""
        ...

    @abstractmethod
    def get_streaming_handler(self, metrics: PrometheusMetrics | None = None) -> Any:
        """Get streaming handler service."""
        ...

    @abstractmethod
    def get_auth_service(self, credentials_manager: CredentialsManager) -> Any:
        """Get authentication service."""
        ...

    @abstractmethod
    def get_proxy_config(self) -> Any:
        """Get proxy configuration service."""
        ...

    @abstractmethod
    def get_http_client(self) -> httpx.AsyncClient:
        """Get HTTP client service."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Close all managed resources during shutdown."""
        ...
