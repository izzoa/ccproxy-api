"""Protocol interfaces for dependency inversion."""

from typing import TYPE_CHECKING, Any, Protocol

from ccproxy.services.adapters.base import BaseAdapter
from ccproxy.services.tracing.interfaces import RequestTracer


if TYPE_CHECKING:
    import httpx

    from ccproxy.plugins.registry import PluginRegistry


class IRequestHandler(Protocol):
    """Protocol for request handling functionality.

    Note: The dispatch_request method has been removed in favor of
    using plugin adapters' handle_request() method directly.
    """

    pass


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
        http_client: "httpx.AsyncClient",
        proxy_service: "IRequestHandler",
        scheduler: Any | None = None,  # Scheduler doesn't have a protocol yet
    ) -> None:
        """Initialize all plugins."""
        ...

    def get_plugin_registry(self) -> "PluginRegistry":
        """Get the internal plugin registry for admin operations."""
        ...

    def get_adapters_dict(self) -> dict[str, BaseAdapter]:
        """Get the adapters dictionary for admin operations."""
        ...
