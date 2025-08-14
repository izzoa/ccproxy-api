"""Plugin protocol for provider plugins."""

from typing import Any, Literal, Protocol, runtime_checkable

from fastapi import APIRouter
from pydantic import BaseModel

from ccproxy.core.services import CoreServices
from ccproxy.models.provider import ProviderConfig
from ccproxy.services.adapters.base import BaseAdapter


class HealthCheckResult(BaseModel):
    """Standardized health check result following IETF format."""

    status: Literal["pass", "warn", "fail"]
    componentId: str  # noqa: N815
    componentType: str = "provider_plugin"  # noqa: N815
    output: str | None = None
    version: str | None = None
    details: dict[str, Any] | None = None


@runtime_checkable
class ProviderPlugin(Protocol):
    """Enhanced protocol for provider plugins."""

    @property
    def name(self) -> str:
        """Plugin name."""
        ...

    @property
    def version(self) -> str:
        """Plugin version."""
        ...

    @property
    def router_prefix(self) -> str:
        """Unique route prefix for this plugin (e.g., '/claude', '/codex')."""
        ...

    async def initialize(self, services: CoreServices) -> None:
        """Initialize plugin with shared services. Called once on startup."""
        ...

    async def shutdown(self) -> None:
        """Perform graceful shutdown. Called once on app shutdown."""
        ...

    def create_adapter(self) -> BaseAdapter:
        """Create adapter instance."""
        ...

    def create_config(self) -> ProviderConfig:
        """Create provider configuration from settings."""
        ...

    async def validate(self) -> bool:
        """Validate plugin is ready."""
        ...

    def get_routes(self) -> APIRouter | None:
        """Get plugin-specific routes (optional)."""
        ...

    async def health_check(self) -> HealthCheckResult:
        """Perform health check following IETF format."""
        ...
