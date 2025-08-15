"""Claude API provider plugin implementation."""

from typing import Any

import structlog
from fastapi import APIRouter

from ccproxy.core.services import CoreServices
from ccproxy.plugins.protocol import HealthCheckResult, ProviderPlugin
from ccproxy.services.adapters.base import BaseAdapter

from .adapter import ClaudeAPIAdapter
from .config import ClaudeAPISettings
from .detection_service import ClaudeAPIDetectionService
from .health import claude_api_health_check
from .routes import router as claude_api_router
from .tasks import ClaudeAPIDetectionRefreshTask


logger = structlog.get_logger(__name__)


class Plugin(ProviderPlugin):
    """Claude API provider plugin.

    This plugin provides direct access to the Anthropic Claude API
    with support for both native Anthropic format and OpenAI-compatible format.
    """

    def __init__(self) -> None:
        """Initialize the Claude API plugin."""
        self._name = "claude_api"
        self._version = "1.0.0"
        self._router_prefix = "/claude-api"
        self._services: CoreServices | None = None
        self._config: ClaudeAPISettings | None = None
        self._adapter: ClaudeAPIAdapter | None = None
        self._credentials_manager: Any | None = None
        self._detection_service: ClaudeAPIDetectionService | None = None

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
        self._config = ClaudeAPISettings.model_validate(plugin_config)

        # Initialize detection service
        self._detection_service = ClaudeAPIDetectionService(services.settings)
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
        self._adapter = ClaudeAPIAdapter(
            http_client=services.http_client,
            logger=services.logger.bind(plugin=self.name),
        )

        # Initialize credentials manager for OAuth token management
        from ccproxy.services.credentials.manager import CredentialsManager

        self._credentials_manager = CredentialsManager()
        logger.info(
            "claude_api_plugin_initialized",
            status="initialized",
            base_url=self._config.base_url,
            models_count=len(self._config.models) if self._config.models else 0,
            has_credentials_manager=True,
        )

    async def shutdown(self) -> None:
        """Cleanup on shutdown."""
        if self._adapter:
            await self._adapter.cleanup()
        logger.info("claude_api_plugin_shutdown", status="shutdown")

    def create_adapter(self) -> BaseAdapter:
        """Create adapter instance.

        Returns:
            ClaudeAPIAdapter instance

        Raises:
            RuntimeError: If plugin not initialized
        """
        if not self._adapter:
            raise RuntimeError("Plugin not initialized")
        return self._adapter

    def create_config(self) -> ClaudeAPISettings:
        """Create provider configuration.

        Returns:
            ClaudeAPISettings instance

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
        """Return Claude API routes.

        Returns:
            FastAPI router with Claude API endpoints
        """
        return claude_api_router

    async def health_check(self) -> HealthCheckResult:
        """Perform health check for Claude API plugin.

        Returns:
            HealthCheckResult with plugin status
        """
        return await claude_api_health_check(
            self._config, self._detection_service, self._credentials_manager
        )

    def get_scheduled_tasks(self) -> list[Any] | None:
        """Get scheduled task definitions.

        Returns:
            List of scheduled task definitions for Claude API plugin
        """
        if not self._detection_service:
            return None

        return [
            {
                "task_name": f"claude_api_detection_refresh_{self.name}",
                "task_type": "claude_api_detection_refresh",
                "task_class": ClaudeAPIDetectionRefreshTask,
                "interval_seconds": 3600,  # Refresh every hour
                "enabled": True,
                "detection_service": self._detection_service,
                "skip_initial_run": True,
            }
        ]
