"""Codex provider plugin implementation."""

from typing import Any

import structlog
from fastapi import APIRouter
from pydantic import BaseModel

from ccproxy.core.services import CoreServices
from ccproxy.models.provider import ProviderConfig
from ccproxy.plugins.protocol import (
    HealthCheckResult,
    ProviderPlugin,
)
from ccproxy.services.adapters.base import BaseAdapter

from .adapter import CodexAdapter
from .config import CodexSettings
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
        self._detection_service: Any | None = None  # Will be CodexDetectionService
        self._auth_manager: Any | None = None  # Will be OpenAITokenManager

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
        summary = {
            "models": "auto",  # Codex discovers models dynamically
        }

        # Add basic authentication status (detailed auth info requires async call)
        if self._auth_manager:
            summary["auth"] = "configured"
        else:
            summary["auth"] = "not_configured"

        # Add CLI information
        if self._detection_service:
            cli_path = self._detection_service.get_cli_path()
            cli_version = self._detection_service.get_version()
            if cli_path and cli_version:
                summary["cli_version"] = cli_version
                summary["cli_path"] = cli_path

                # Determine CLI source
                if isinstance(cli_path, list) and len(cli_path) > 1:
                    summary["cli_source"] = "package_manager"
                    summary["package_manager"] = cli_path[0]
                else:
                    summary["cli_source"] = "in_path"
                    if isinstance(cli_path, list):
                        summary["cli_path"] = cli_path[0]

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
