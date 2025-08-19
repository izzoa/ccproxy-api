"""Protocol interfaces for dependency inversion."""

from typing import Any, Protocol

from fastapi import Request
from fastapi.responses import Response, StreamingResponse

from ccproxy.services.adapters.base import BaseAdapter
from ccproxy.services.handler_config import HandlerConfig
from ccproxy.services.tracing.interfaces import RequestTracer


class IRequestHandler(Protocol):
    """Protocol for request handling functionality."""

    async def dispatch_request(
        self,
        request: Request,
        handler_config: HandlerConfig,
        provider_name: str | None = None,
    ) -> Response | StreamingResponse:
        """Handle a proxy request."""
        ...


class IPluginRegistry(Protocol):
    """Protocol for plugin registry functionality."""

    def get_adapter(self, provider_name: str) -> BaseAdapter | None:
        """Get an adapter for a specific provider."""
        ...

    def get_tracer(self, provider_name: str) -> RequestTracer | None:
        """Get a tracer for a specific provider."""
        ...

    def list_providers(self) -> list[str]:
        """List all available providers."""
        ...

    async def initialize_plugins(
        self,
        http_client: Any,
        proxy_service: Any,
        scheduler: Any | None = None,
    ) -> None:
        """Initialize all plugins."""
        ...

    def get_plugin_registry(self) -> Any:
        """Get the internal plugin registry for admin operations."""
        ...

    def get_adapters_dict(self) -> dict[str, BaseAdapter]:
        """Get the adapters dictionary for admin operations."""
        ...
