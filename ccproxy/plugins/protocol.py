"""Plugin protocol for provider plugins."""

from typing import Protocol, runtime_checkable

from ccproxy.models.provider import ProviderConfig
from ccproxy.services.adapters.base import BaseAdapter


@runtime_checkable
class ProviderPlugin(Protocol):
    """Protocol for provider plugins."""

    @property
    def name(self) -> str:
        """Plugin name."""
        ...

    @property
    def version(self) -> str:
        """Plugin version."""
        ...

    def create_adapter(self) -> BaseAdapter:
        """Create adapter instance."""
        ...

    def create_config(self) -> ProviderConfig:
        """Create provider configuration."""
        ...

    async def validate(self) -> bool:
        """Validate plugin is ready."""
        ...
