"""Claude SDK provider plugin implementation."""

from typing import Any

import httpx
import structlog
from fastapi import APIRouter
from pydantic import BaseModel

from ccproxy.core.services import CoreServices
from ccproxy.plugins.protocol import HealthCheckResult, ProviderPlugin
from ccproxy.services.adapters.base import BaseAdapter
from ccproxy.utils.binary_resolver import BinaryResolver
from ccproxy.utils.cli_logging import log_plugin_summary

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

        # Check CLI status - plugin requires Claude CLI to be available
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
            error_msg = "Claude CLI not found in PATH or common locations - SDK plugin requires installed CLI"
            logger.error(
                "claude_sdk_plugin_initialization_failed",
                status="failed",
                error=error_msg,
            )
            raise RuntimeError(error_msg)

        # Initialize adapter with plugin config and proxy service
        self._adapter = ClaudeSDKAdapter(
            config=self._config, proxy_service=services.proxy_service
        )

        # Set detection service on adapter
        self._adapter.set_detection_service(self._detection_service)

        # Log comprehensive configuration
        session_pool_config = {}
        if self._config.sdk_session_pool:
            session_pool_config = {
                "enabled": self._config.sdk_session_pool.enabled,
                "session_ttl": self._config.sdk_session_pool.session_ttl,
                "max_sessions": self._config.sdk_session_pool.max_sessions,
                "cleanup_interval": self._config.sdk_session_pool.cleanup_interval,
                "idle_threshold": self._config.sdk_session_pool.idle_threshold,
            }

        # Get runtime session ID from adapter if available
        runtime_session_id = None
        if hasattr(self._adapter, "_runtime_default_session_id"):
            runtime_session_id = self._adapter._runtime_default_session_id

        logger.debug(
            "claude_sdk_plugin_initialized",
            status="initialized",
            cli_available=self._detection_service.is_claude_available(),
            cli_path=self._detection_service.get_cli_path(),
            models_count=len(self._config.models),
            # Session configuration
            session_pool=session_pool_config,
            default_session_id=self._config.default_session_id,
            auto_generate_default_session=self._config.auto_generate_default_session,
            runtime_session_id=runtime_session_id,
            # SDK behavior settings
            sdk_message_mode=self._config.sdk_message_mode.value,
            include_system_messages_in_stream=self._config.include_system_messages_in_stream,
            pretty_format=self._config.pretty_format,
            # Performance settings
            max_tokens_default=self._config.max_tokens_default,
            temperature_default=self._config.temperature_default,
        )

        # Log plugin summary with dynamic CLI logging
        log_plugin_summary(self.get_summary(), self.name)

    def get_summary(self) -> dict[str, Any]:
        """Get plugin summary for consolidated logging."""
        summary: dict[str, Any] = {
            "router_prefix": self.router_prefix,
        }

        if self._config and self._config.models:
            summary["models"] = len(self._config.models)
        else:
            summary["models"] = 0

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

        # Add session pool configuration
        if self._config and self._config.session_pool_enabled:
            summary["sessions_pool"] = "enabled"
            if (
                hasattr(self._config, "session_pool_config")
                and self._config.session_pool_config
            ):
                pool_config = self._config.session_pool_config
                summary["sessions_max"] = pool_config.max_sessions
                summary["sessions_ttl"] = pool_config.session_ttl

        return summary

    async def shutdown(self) -> None:
        """Cleanup on shutdown."""
        if self._adapter:
            await self._adapter.close()  # Use close() instead of cleanup()
        logger.debug("claude_sdk_plugin_shutdown", status="shutdown")

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

    async def get_oauth_client(self) -> None:
        """Claude SDK doesn't use OAuth, uses cookie-based auth instead.

        Returns:
            None - Claude SDK uses different authentication mechanism
        """
        return None

    async def get_profile_info(self) -> dict[str, Any] | None:
        """Get Claude SDK profile information from stored credentials.

        Returns:
            Dictionary containing Claude SDK profile information
        """
        try:
            # Claude SDK uses credentials manager directly from services
            if not self._services:
                return None

            # Get credentials manager from services
            from ccproxy.services.credentials import CredentialsManager

            settings = self._services.settings
            credentials_manager = CredentialsManager(config=settings.auth)

            # Get profile using credentials manager
            profile = await credentials_manager.get_account_profile()
            if not profile:
                # Try to validate and get credentials info
                validation = await credentials_manager.validate()
                if validation.valid and validation.credentials:
                    # Build profile info from credentials
                    oauth = validation.credentials.claude_ai_oauth
                    profile_info: dict[str, Any] = {
                        "provider": "claude-sdk",
                        "subscription_type": oauth.subscription_type,
                        "authenticated": True,
                    }

                    if oauth.scopes:
                        profile_info["scopes"] = oauth.scopes

                    if oauth.expires_at:
                        from datetime import UTC, datetime

                        profile_info["expires_at"] = datetime.fromtimestamp(
                            oauth.expires_at / 1000, tz=UTC
                        ).isoformat()

                    return profile_info

            if profile:
                result_info: dict[str, Any] = {"provider": "claude-sdk"}

                if profile.organization:
                    result_info.update(
                        {
                            "organization_name": profile.organization.name,
                            "organization_type": profile.organization.organization_type,
                        }
                    )

                if profile.account:
                    result_info.update(
                        {
                            "email": profile.account.email,
                            "full_name": profile.account.full_name,
                            "display_name": profile.account.display_name,
                            "has_claude_pro": profile.account.has_claude_pro,
                        }
                    )

                return result_info

        except httpx.HTTPError as e:
            logger.debug(
                "claude_sdk_profile_http_error",
                error=str(e),
                exc_info=e,
            )
        except ValueError as e:
            logger.debug(
                "claude_sdk_profile_validation_error",
                error=str(e),
                exc_info=e,
            )
        except Exception as e:
            logger.debug(
                "claude_sdk_profile_unexpected_error",
                error=str(e),
                exc_info=e,
            )

        return None

    def get_auth_commands(self) -> list[Any] | None:
        """Get Claude SDK specific auth command extensions.

        Returns:
            None - Claude SDK auth is handled by main auth commands
        """
        return None
