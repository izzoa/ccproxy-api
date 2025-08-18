"""Codex provider plugin implementation."""

from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    pass
from fastapi import APIRouter
from pydantic import BaseModel

from ccproxy.auth.openai.credentials import OpenAITokenManager
from ccproxy.core.services import CoreServices
from ccproxy.models.provider import ProviderConfig
from ccproxy.plugins.protocol import (
    HealthCheckResult,
    ProviderPlugin,
)
from ccproxy.services.adapters.base import BaseAdapter
from ccproxy.utils.binary_resolver import BinaryResolver

from .adapter import CodexAdapter
from .config import CodexSettings
from .detection_service import CodexDetectionService
from .health import codex_health_check
from .routes import router as codex_router
from .tasks import CodexDetectionRefreshTask


logger = structlog.get_logger(__name__)


class Plugin(ProviderPlugin):
    """Codex provider plugin."""

    def __init__(self) -> None:
        self._name = "codex"
        self._version = "1.0.0"
        self._router_prefix = "/api/codex"
        self._adapter: CodexAdapter | None = None
        self._config: CodexSettings | None = None
        self._services: CoreServices | None = None
        self._detection_service: CodexDetectionService | None = None
        self._auth_manager: OpenAITokenManager | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def version(self) -> str:
        return self._version

    @property
    def router_prefix(self) -> str:
        return self._router_prefix

    async def initialize(self, services: CoreServices) -> None:
        """Initialize plugin with shared services."""
        self._services = services

        # Load plugin-specific configuration from plugins dictionary
        plugin_config = getattr(services.settings, "plugins", {}).get(self.name, {})

        # If no config provided, use defaults from CodexSettings
        if not plugin_config:
            plugin_config = {
                "name": self.name,
                "base_url": "https://chatgpt.com/backend-api/codex",
                "supports_streaming": True,
                "requires_auth": True,
                "auth_type": "oauth",
                "models": ["gpt-5"],
            }

        self._config = CodexSettings.model_validate(plugin_config)

        # Initialize adapter with shared HTTP client
        self._adapter = CodexAdapter(
            http_client=services.http_client,
            logger=logger.bind(plugin=self.name),
        )

        # Set up authentication manager for the adapter
        from ccproxy.auth.openai import OpenAITokenManager

        auth_manager = OpenAITokenManager()
        logger.debug(
            "codex_plugin_auth_setup",
            auth_manager_type=type(auth_manager).__name__,
            storage_location=auth_manager.get_storage_location(),
        )

        # Check if we have valid credentials
        has_creds = await auth_manager.has_credentials()
        if has_creds:
            token = await auth_manager.get_valid_token()
            logger.debug(
                "codex_plugin_auth_status",
                has_credentials=True,
                has_valid_token=bool(token),
                token_preview=token[:20] + "..." if token else None,
            )
        else:
            logger.warning(
                "codex_plugin_no_auth",
                msg="No OpenAI credentials found. Run 'ccproxy auth login --provider openai' to authenticate.",
            )

        self._adapter.set_auth_manager(auth_manager)
        self._auth_manager = auth_manager  # Store for health checks
        logger.debug("codex_plugin_auth_manager_set", adapter_has_auth=True)

        # Set up detection service for the adapter
        from .detection_service import CodexDetectionService

        detection_service = CodexDetectionService(services.settings)

        # Initialize detection service to capture Codex CLI headers
        logger.debug("codex_plugin_initializing_detection")
        try:
            await detection_service.initialize_detection()

            # Log Codex CLI status
            version = detection_service.get_version()
            binary_path = detection_service.get_binary_path()

            if binary_path:
                logger.debug(
                    "codex_cli_available",
                    status="available",
                    version=version,
                    binary_path=binary_path,
                )
            else:
                logger.warning(
                    "codex_cli_not_found",
                    status="not_found",
                    msg="Codex CLI not found in PATH or common locations",
                )

            logger.debug(
                "codex_plugin_detection_initialized",
                has_cached_data=detection_service.get_cached_data() is not None,
                version=version,
            )
        except Exception as e:
            logger.warning(
                "codex_plugin_detection_initialization_failed",
                error=str(e),
                msg="Using fallback Codex instructions",
            )

        self._adapter.set_detection_service(detection_service)
        self._detection_service = detection_service  # Store for health checks
        logger.debug("codex_plugin_detection_service_set", adapter_has_detection=True)

    def get_summary(self) -> dict[str, Any]:
        """Get plugin summary for consolidated logging."""
        summary: dict[str, Any] = {
            "router_prefix": self.router_prefix,
            "models": "auto",  # Codex discovers models dynamically
        }

        # Add basic authentication status (detailed auth info requires async call)
        if self._auth_manager:
            summary["auth"] = "configured"
        else:
            summary["auth"] = "not_configured"

        # Add CLI information using common format
        if self._detection_service:
            cli_version = self._detection_service.get_version()
            cli_path = self._detection_service.get_cli_path()

            # Create CLI info using common format
            resolver = BinaryResolver()
            cli_info = resolver.get_cli_info("codex", "@openai/codex", cli_version)

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
            summary["cli_info"] = {"codex": cli_info}

        return summary

    async def get_auth_summary(self) -> dict[str, Any]:
        """Get detailed authentication status (async version for use in plugin manager)."""
        if not self._auth_manager:
            return {"auth": "not_configured"}

        try:
            auth_status = await self._auth_manager.get_auth_status()
            summary = {"auth": "not_configured"}

            if auth_status.get("auth_configured"):
                if auth_status.get("token_available"):
                    summary["auth"] = "authenticated"
                    if "time_remaining" in auth_status:
                        summary["auth_expires"] = auth_status["time_remaining"]
                    if "token_expired" in auth_status:
                        summary["auth_expired"] = auth_status["token_expired"]
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

    def create_adapter(self) -> BaseAdapter:
        if not self._adapter:
            raise RuntimeError("Plugin not initialized")
        return self._adapter

    def create_config(self) -> ProviderConfig:
        if not self._config:
            raise RuntimeError("Plugin not initialized")
        return self._config

    async def validate(self) -> bool:
        """Check if Codex configuration is valid."""
        # Always return True - actual validation happens during initialization
        # The plugin system calls validate() before initialize(), so we can't
        # check config here since it hasn't been loaded yet
        # The detection service will handle CLI availability checking
        return True

    def get_routes(self) -> APIRouter | None:
        """Return Codex-specific routes."""
        # Return the router defined in routes.py
        return codex_router

    async def health_check(self) -> HealthCheckResult:
        """Perform health check for Codex plugin."""
        return await codex_health_check(
            self._config, self._detection_service, self._auth_manager
        )

    def get_scheduled_tasks(self) -> list[dict[str, Any]] | None:  # type: ignore[override]
        """Get scheduled task definitions for Codex plugin.

        Returns:
            List with detection refresh task or None if detection service not available
        """
        if not self._detection_service:
            return None

        # Create the task definition with detection_service
        task_def = {
            "task_name": f"codex_detection_refresh_{self.name}",
            "task_type": "codex_detection_refresh",
            "task_class": CodexDetectionRefreshTask,
            "interval_seconds": 3600.0,  # Refresh every hour
            "enabled": True,
            "detection_service": self._detection_service,
            "skip_initial_run": True,
        }

        return [task_def]

    def get_config_class(self) -> type[BaseModel] | None:
        """Get the Pydantic configuration model for this plugin.

        Returns:
            CodexSettings class for plugin configuration
        """
        return CodexSettings

    async def get_oauth_client(self) -> Any:
        """Get OAuth client for Codex authentication.

        Returns:
            OpenAI OAuth client instance configured for Codex
        """
        from ccproxy.auth.openai import OpenAIOAuthClient

        if not self._config:
            raise RuntimeError("Plugin not initialized")

        return OpenAIOAuthClient(self._config, self._auth_manager)

    async def get_profile_info(self) -> dict[str, Any] | None:
        """Get Codex-specific profile information from stored credentials.

        Returns:
            Dictionary containing Codex-specific profile information
        """
        try:
            import base64
            import json

            # Get access token from stored credentials
            if not self._auth_manager:
                return None

            access_token = await self._auth_manager.get_valid_token()
            if not access_token:
                return None

            # For OpenAI/Codex, extract info from JWT token
            parts = access_token.split(".")
            if len(parts) != 3:
                return None

            # Decode JWT payload
            payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_b64))

            profile_info = {}

            # Extract OpenAI-specific information
            openai_auth = payload.get("https://api.openai.com/auth", {})
            if openai_auth:
                if "email" in payload:
                    profile_info["email"] = payload["email"]
                    profile_info["email_verified"] = payload.get(
                        "email_verified", False
                    )

                if openai_auth.get("chatgpt_plan_type"):
                    profile_info["plan_type"] = openai_auth["chatgpt_plan_type"].upper()

                if openai_auth.get("chatgpt_user_id"):
                    profile_info["user_id"] = openai_auth["chatgpt_user_id"]

                # Subscription info
                if openai_auth.get("chatgpt_subscription_active_start"):
                    profile_info["subscription_start"] = openai_auth[
                        "chatgpt_subscription_active_start"
                    ]
                if openai_auth.get("chatgpt_subscription_active_until"):
                    profile_info["subscription_until"] = openai_auth[
                        "chatgpt_subscription_active_until"
                    ]

                # Organizations
                orgs = openai_auth.get("organizations", [])
                if orgs:
                    for org in orgs:
                        if org.get("is_default"):
                            profile_info["organization"] = org.get("title", "Unknown")
                            profile_info["organization_role"] = org.get(
                                "role", "member"
                            )
                            profile_info["organization_id"] = org.get("id", "Unknown")
                            break

            return profile_info if profile_info else None

        except Exception as e:
            logger.debug(f"Failed to get Codex profile info: {e}")
            return None

    def get_auth_commands(self) -> list[Any] | None:
        """Get Codex-specific auth command extensions.

        Returns:
            List of auth command definitions or None
        """
        # Codex plugin doesn't need custom auth commands for now
        return None
