"""Claude API provider plugin implementation."""

from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    pass
from fastapi import APIRouter
from pydantic import BaseModel

from ccproxy.core.services import CoreServices
from ccproxy.plugins.protocol import (
    HealthCheckResult,
    OAuthClientProtocol,
    ProviderPlugin,
)
from ccproxy.services.adapters.base import BaseAdapter
from ccproxy.utils.binary_resolver import BinaryResolver
from ccproxy.utils.cli_logging import log_plugin_summary

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
        self._router_prefix = "/api"
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
            logger.debug(
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

        # Initialize credentials manager for OAuth token management
        from ccproxy.services.credentials.manager import CredentialsManager

        self._credentials_manager = CredentialsManager()

        # Initialize adapter with all required dependencies
        self._adapter = ClaudeAPIAdapter(
            proxy_service=None,  # Will be set by plugin manager via set_proxy_service
            auth_manager=self._credentials_manager,
            detection_service=self._detection_service,
            http_client=services.http_client,
            logger=services.logger.bind(plugin=self.name),
        )
        logger.debug(
            "claude_api_plugin_initialized",
            status="initialized",
            base_url=self._config.base_url,
            models_count=len(self._config.models) if self._config.models else 0,
            has_credentials_manager=True,
        )

        # Log plugin summary with dynamic CLI logging
        log_plugin_summary(self.get_summary(), self.name)

    def get_summary(self) -> dict[str, Any]:
        """Get plugin summary for consolidated logging."""
        summary: dict[str, Any] = {
            "router_prefix": self.router_prefix,
        }

        if self._config:
            if self._config.models:
                summary["models"] = len(self._config.models)
            else:
                summary["models"] = 0
            summary["base_url"] = self._config.base_url
        else:
            summary["models"] = 0

        # Add basic authentication status (detailed auth info requires async call)
        if self._credentials_manager:
            summary["auth"] = "configured"
        else:
            summary["auth"] = "not_configured"

        # Add CLI information using common format
        if self._detection_service:
            cli_version = self._detection_service.get_version()
            cli_path = self._detection_service.get_cli_path()

            # Create CLI info using common format
            resolver = BinaryResolver()
            cli_info = resolver.get_cli_info(
                "claude", "@anthropic-ai/claude-code", cli_version
            )

            # Override with actual detection service results if available
            if cli_path:
                cli_info["command"] = cli_path
                cli_info["is_available"] = True
                if isinstance(cli_path, list) and len(cli_path) > 1:
                    cli_info["source"] = "package_manager"
                    cli_info["package_manager"] = cli_path[0]
                    cli_info["path"] = None
                else:
                    cli_info["source"] = "path"
                    cli_info["path"] = (
                        cli_path[0] if isinstance(cli_path, list) else cli_path
                    )
                    cli_info["package_manager"] = None

            # Store CLI info in a structured way for dynamic logging
            summary["cli_info"] = {"claude": cli_info}

        return summary

    async def get_auth_summary(self) -> dict[str, Any]:
        """Get detailed authentication status (async version for use in plugin manager)."""
        if not self._credentials_manager:
            return {"auth": "not_configured"}

        try:
            auth_status = await self._credentials_manager.get_auth_status()
            summary = {"auth": "not_configured"}

            if auth_status.get("auth_configured"):
                if auth_status.get("token_available"):
                    summary["auth"] = "authenticated"
                    if "time_remaining" in auth_status:
                        summary["auth_expires"] = auth_status["time_remaining"]
                    if "token_expired" in auth_status:
                        summary["auth_expired"] = auth_status["token_expired"]
                    if "subscription_type" in auth_status:
                        summary["subscription"] = auth_status["subscription_type"]
                else:
                    summary["auth"] = "no_token"
            else:
                summary["auth"] = "not_configured"

            return summary
        except Exception:
            return {"auth": "status_error"}

    async def shutdown(self) -> None:
        """Cleanup on shutdown."""
        if self._adapter:
            await self._adapter.cleanup()
        logger.debug("claude_api_plugin_shutdown", status="shutdown")

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

    def get_config_class(self) -> type[BaseModel] | None:
        """Get the Pydantic configuration model for this plugin.

        Returns:
            ClaudeAPISettings class for plugin configuration
        """
        return ClaudeAPISettings

    async def get_oauth_client(self) -> "OAuthClientProtocol | None":
        """Get OAuth client for Claude API authentication.

        Returns:
            Claude OAuth client instance
        """
        from ccproxy.services.credentials.oauth_client import OAuthClient

        return OAuthClient()

    async def get_profile_info(self) -> dict[str, Any] | None:
        """Get Claude-specific profile information from stored credentials.

        Returns:
            Dictionary containing Claude-specific profile information
        """
        try:
            if not self._credentials_manager:
                return None

            # Get profile using credentials manager
            profile = await self._credentials_manager.get_account_profile()
            if not profile:
                # Try to fetch fresh profile
                profile = await self._credentials_manager.fetch_user_profile()

            if profile:
                profile_info = {}

                if profile.organization:
                    profile_info.update(
                        {
                            "organization_name": profile.organization.name,
                            "organization_type": profile.organization.organization_type,
                            "billing_type": profile.organization.billing_type,
                            "rate_limit_tier": profile.organization.rate_limit_tier,
                        }
                    )

                if profile.account:
                    profile_info.update(
                        {
                            "email": profile.account.email,
                            "full_name": profile.account.full_name,
                            "display_name": profile.account.display_name,
                            "has_claude_pro": profile.account.has_claude_pro,
                            "has_claude_max": profile.account.has_claude_max,
                        }
                    )

                return profile_info

        except Exception as e:
            logger.debug(f"Failed to get Claude profile info: {e}")

        return None

    def get_auth_commands(self) -> list[Any] | None:
        """Get Claude-specific auth command extensions.

        Returns:
            List of auth command definitions or None
        """
        # Claude API plugin doesn't need custom auth commands for now
        return None
