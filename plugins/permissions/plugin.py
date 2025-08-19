"""Permission plugin registration and lifecycle management."""

from typing import Any

import structlog
from fastapi import APIRouter
from pydantic import BaseModel

from ccproxy.core.services import CoreServices
from ccproxy.models.provider import ProviderConfig
from ccproxy.plugins.protocol import HealthCheckResult, ProviderPlugin
from ccproxy.services.adapters.base import BaseAdapter

from .config import PermissionsConfig
from .mcp import mcp_router
from .routes import router
from .service import get_permission_service


logger = structlog.get_logger(__name__)


class Plugin(ProviderPlugin):
    """Permissions plugin providing authorization services.

    This is a system plugin that provides permission management,
    not a provider plugin, so many provider-specific methods return None.
    """

    def __init__(self) -> None:
        """Initialize the permissions plugin."""
        self._name = "permissions"
        self._version = "1.0.0"
        self._router_prefix = "/permissions"
        self._service = get_permission_service()
        self._config: PermissionsConfig | None = None
        self._logger = logger.bind(plugin=self._name)

    @property
    def name(self) -> str:
        """Plugin name."""
        return self._name

    @property
    def version(self) -> str:
        """Plugin version."""
        return self._version

    @property
    def router_prefix(self) -> str:
        """Route prefix for this plugin."""
        return self._router_prefix

    async def initialize(self, services: CoreServices) -> None:
        """Initialize plugin with shared services.

        Args:
            services: Core services container
        """
        self._logger.info("initializing_permissions_plugin")

        # Store services for later use
        self._services = services

        # Load plugin configuration
        plugin_config = services.get_plugin_config(self.name)
        self._config = PermissionsConfig.model_validate(plugin_config)

        # Start the permission service if enabled
        if self._config.enabled:
            # Update service timeout from config
            self._service._timeout_seconds = self._config.timeout_seconds
            await self._service.start()
            self._logger.info(
                "permission_service_started",
                timeout_seconds=self._config.timeout_seconds,
                terminal_ui=self._config.enable_terminal_ui,
                sse_stream=self._config.enable_sse_stream,
            )
        else:
            self._logger.info("permission_service_disabled")

    async def shutdown(self) -> None:
        """Shutdown the plugin and cleanup resources."""
        self._logger.info("shutting_down_permissions_plugin")

        # Stop the permission service
        await self._service.stop()

        self._logger.info("permissions_plugin_shutdown_complete")

    def create_adapter(self) -> BaseAdapter:
        """Permissions plugin doesn't need an adapter."""
        # This is a system plugin, not a provider plugin
        # Return a dummy adapter to satisfy the protocol
        from ccproxy.services.adapters.base import BaseAdapter

        class DummyAdapter(BaseAdapter):
            async def handle_request(self, *args: Any, **kwargs: Any) -> Any:
                pass

            async def handle_streaming(self, *args: Any, **kwargs: Any) -> Any:
                pass

        return DummyAdapter()

    def create_config(self) -> ProviderConfig:
        """Permissions plugin doesn't need provider config."""
        # Return minimal config to satisfy protocol
        return ProviderConfig(
            name=self.name,
            base_url="",  # No external URL
        )

    async def validate(self) -> bool:
        """Validate plugin is ready."""
        # Permissions plugin is always valid if service exists
        return self._service is not None

    def get_routes(self) -> APIRouter | dict[str, APIRouter] | None:
        """Get plugin routes.

        Returns a dictionary mapping mount paths to routers:
        - /permissions: SSE streaming and permission management routes
        - /mcp: MCP protocol routes for Claude Code
        """
        routes = {}

        # Add SSE streaming routes at /permissions if enabled
        if self._config and self._config.enable_sse_stream:
            routes["/permissions"] = router

        # Always add MCP routes at /mcp root (they're essential for Claude Code)
        routes["/mcp"] = mcp_router

        return routes

    async def health_check(self) -> HealthCheckResult:
        """Perform health check."""
        try:
            # Check if service is running
            pending_count = len(await self._service.get_pending_requests())
            return HealthCheckResult(
                status="pass",
                componentId=self.name,
                componentType="system_plugin",
                output=f"Service running with {pending_count} pending requests",
                version=self.version,
                details={
                    "pending_requests": pending_count,
                    "enabled": self._config.enabled if self._config else False,
                },
            )
        except Exception as e:
            return HealthCheckResult(
                status="fail",
                componentId=self.name,
                componentType="system_plugin",
                output=str(e),
                version=self.version,
            )

    def get_scheduled_tasks(self) -> list[Any] | None:
        """Permissions plugin doesn't need scheduled tasks."""
        return None

    def get_config_class(self) -> type[BaseModel] | None:
        """Get configuration class."""
        return PermissionsConfig

    async def get_oauth_client(self) -> Any | None:
        """Permissions plugin doesn't use OAuth."""
        return None

    async def get_profile_info(self) -> dict[str, Any] | None:
        """Permissions plugin doesn't have profile info."""
        return None

    def get_auth_commands(self) -> list[Any] | None:
        """Permissions plugin doesn't have auth commands."""
        return None

    async def get_auth_summary(self) -> dict[str, Any]:
        """Get authentication summary for the plugin.

        Returns:
            Dictionary with auth status (not applicable for permissions plugin)
        """
        return {
            "auth": "not_applicable",
            "description": "Permissions plugin does not require authentication",
        }
