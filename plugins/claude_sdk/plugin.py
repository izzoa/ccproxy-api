"""Claude SDK provider plugin implementation."""

from typing import Any

import structlog
from fastapi import APIRouter
from pydantic import BaseModel

from ccproxy.core.services import CoreServices
from ccproxy.plugins.protocol import HealthCheckResult, ProviderPlugin
from ccproxy.services.adapters.base import BaseAdapter

from .adapter import ClaudeSDKAdapter
from .config import ClaudeSDKSettings
from .detection_service import ClaudeSDKDetectionService
from .health import claude_sdk_health_check
from .routes import router as claude_sdk_router
from .tasks import ClaudeSDKDetectionRefreshTask


logger = structlog.get_logger(__name__)


class Plugin(ProviderPlugin):
    """Claude SDK provider plugin.

    This plugin provides access to Claude through the Claude Code SDK,
    enabling MCP tools and other SDK-specific features like session management.
    """

    def __init__(self) -> None:
        """Initialize the Claude SDK plugin."""
        self._name = "claude_sdk"
        self._version = "1.0.0"
        self._router_prefix = "/claude"
        self._services: CoreServices | None = None
        self._config: ClaudeSDKSettings | None = None
        self._adapter: ClaudeSDKAdapter | None = None
        self._detection_service: ClaudeSDKDetectionService | None = None

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
        """Unique route prefix for this plugin."""
        return self._router_prefix

    async def initialize(self, services: CoreServices) -> None:
        """Initialize plugin with shared services.

        Args:
            services: Core services container with shared resources
        """
        self._services = services

        # Load plugin-specific configuration
        plugin_config = services.get_plugin_config(self.name)
        self._config = ClaudeSDKSettings.model_validate(plugin_config)

        # Initialize detection service
        self._detection_service = ClaudeSDKDetectionService(services.settings)
        await self._detection_service.initialize_detection()

        # Log CLI status
        version = self._detection_service.get_version()
        cli_path = self._detection_service.get_cli_path()

        if cli_path:
            logger.info(
                "claude_cli_available",
                status="available",
                version=version,
                cli_path=cli_path,
            )
        else:
            logger.warning(
                "claude_cli_not_found",
                status="not_found",
                msg="Claude CLI not found in PATH or common locations",
            )

        # Initialize adapter with shared HTTP client
        # Create adapter - simplified version doesn't need http_client or logger
        self._adapter = ClaudeSDKAdapter()

        # Set detection service on adapter
        self._adapter.set_detection_service(self._detection_service)

        logger.info(
            "claude_sdk_plugin_initialized",
            status="initialized",
            cli_available=self._detection_service.is_claude_available(),
            models_count=len(self._config.models),
            session_pool_enabled=self._config.session_pool_enabled,
        )

    async def shutdown(self) -> None:
        """Cleanup on shutdown."""
        if self._adapter:
            await self._adapter.close()  # Use close() instead of cleanup()
        logger.info("claude_sdk_plugin_shutdown", status="shutdown")

    def create_adapter(self) -> BaseAdapter:
        """Create adapter instance.

        Returns:
            ClaudeSDKAdapter instance

        Raises:
            RuntimeError: If plugin not initialized
        """
        if not self._adapter:
            raise RuntimeError("Plugin not initialized")
        return self._adapter

    def create_config(self) -> ClaudeSDKSettings:
        """Create provider configuration.

        Returns:
            ClaudeSDKSettings instance

        Raises:
            RuntimeError: If plugin not initialized
        """
        if not self._config:
            raise RuntimeError("Plugin not initialized")
        return self._config

    async def validate(self) -> bool:
        """Validate plugin is ready.

        Always returns True - actual validation happens during initialization.
        The plugin system calls validate() before initialize(), so we can't
        check config here since it hasn't been loaded yet.

        Returns:
            True - validation happens during initialization
        """
        return True

    def get_routes(self) -> APIRouter | None:
        """Return Claude SDK routes.

        Returns:
            FastAPI router with Claude SDK endpoints
        """
        return claude_sdk_router

    async def health_check(self) -> HealthCheckResult:
        """Perform health check for Claude SDK plugin.

        Returns:
            HealthCheckResult with plugin status
        """
        return await claude_sdk_health_check(self._config, self._detection_service)

    def get_scheduled_tasks(self) -> list[dict[str, Any]] | None:  # type: ignore[override]
        """Get scheduled task definitions.

        Returns:
            List of scheduled task definitions for Claude SDK plugin
        """
        if not self._detection_service:
            return None

        return [
            {
                "task_name": f"claude_sdk_detection_refresh_{self.name}",
                "task_type": "claude_sdk_detection_refresh",
                "task_class": ClaudeSDKDetectionRefreshTask,
                "interval_seconds": 3600,  # Refresh every hour
                "enabled": True,
                "detection_service": self._detection_service,
                "skip_initial_run": True,
            }
        ]

    def get_config_class(self) -> type[BaseModel] | None:
        """Get the Pydantic configuration model for this plugin.

        Returns:
            ClaudeSDKSettings class for plugin configuration
        """
        return ClaudeSDKSettings
