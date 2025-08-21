"""Raw HTTP Logger plugin for debugging HTTP requests and responses."""

from typing import Any

import structlog
from pydantic import BaseModel

from ccproxy.core.services import CoreServices
from ccproxy.plugins.protocol import HealthCheckResult, SystemPlugin

from .config import RawHTTPLoggerConfig
from .logger import RawHTTPLogger
from .middleware import RawHTTPLoggingMiddleware
from .transport import LoggingHTTPTransport


logger = structlog.get_logger(__name__)


class Plugin(SystemPlugin):
    """Raw HTTP Logger plugin providing transport-level logging.

    This is a system plugin that provides raw HTTP logging for debugging purposes.
    """

    def __init__(self) -> None:
        """Initialize the raw HTTP logger plugin."""
        self._name = "raw_http_logger"
        self._version = "1.0.0"
        self._router_prefix = "/raw-http-logger"  # Not used but required by protocol
        self._config: RawHTTPLoggerConfig | None = None
        self._logger_instance: RawHTTPLogger | None = None
        self._original_transport: Any = None
        self._services: CoreServices | None = None
        self._middleware_added = False

    @property
    def name(self) -> str:
        """Plugin name."""
        return self._name

    @property
    def version(self) -> str:
        """Plugin version."""
        return self._version

    @property
    def dependencies(self) -> list[str]:
        """List of plugin names this plugin depends on."""
        return []  # No dependencies

    @property
    def router_prefix(self) -> str:
        """Route prefix for this plugin."""
        return self._router_prefix

    async def initialize(self, services: CoreServices) -> None:
        """Initialize plugin with shared services.

        Args:
            services: Core services container
        """
        logger.info("initializing_raw_http_logger_plugin")

        self._services = services

        # Load plugin configuration
        plugin_config = services.get_plugin_config(self.name)
        self._config = RawHTTPLoggerConfig.model_validate(plugin_config)

        # Create logger instance with configuration
        self._logger_instance = RawHTTPLogger(self._config)

        if self._config.enabled:
            # Wrap HTTP client transport for provider logging
            await self._wrap_http_client_transport(services)

            # Note: ASGI middleware will be added separately through app
            # We'll need to expose a method for the app to call

            logger.info(
                "raw_http_logger_enabled",
                log_dir=self._config.log_dir,
                log_client_request=self._config.log_client_request,
                log_client_response=self._config.log_client_response,
                log_provider_request=self._config.log_provider_request,
                log_provider_response=self._config.log_provider_response,
                max_body_size=self._config.max_body_size,
                exclude_paths=self._config.exclude_paths,
                exclude_headers=self._config.exclude_headers,
            )
        else:
            logger.info("raw_http_logger_disabled")

    async def _wrap_http_client_transport(self, services: CoreServices) -> None:
        """Wrap the shared HTTP client's transport with logging.

        Args:
            services: Core services container
        """
        if not services.http_client:
            logger.warning("no_http_client_to_wrap")
            return

        # Get the current transport
        current_transport = services.http_client._transport

        # Only wrap if not already wrapped
        if not isinstance(current_transport, LoggingHTTPTransport):
            # Store original for potential unwrapping
            self._original_transport = current_transport

            # Create and set logging transport
            logging_transport = LoggingHTTPTransport(
                wrapped_transport=current_transport, logger=self._logger_instance
            )
            services.http_client._transport = logging_transport

            logger.debug("http_client_transport_wrapped")

    def create_middleware(self) -> RawHTTPLoggingMiddleware:
        """Create ASGI middleware instance.

        Returns:
            RawHTTPLoggingMiddleware instance configured with plugin settings
        """
        if not self._logger_instance:
            # Create with default config if not initialized
            self._logger_instance = RawHTTPLogger(self._config)

        return RawHTTPLoggingMiddleware(
            app=None,  # Will be set by FastAPI
            logger=self._logger_instance,
        )

    def is_enabled(self) -> bool:
        """Check if logging is enabled.

        Returns:
            True if logging is enabled, False otherwise
        """
        return self._config.enabled if self._config else False

    async def shutdown(self) -> None:
        """Shutdown the plugin and cleanup resources."""
        logger.info("shutting_down_raw_http_logger_plugin")

        # Restore original transport if we wrapped it
        if self._services and self._services.http_client and self._original_transport:
            self._services.http_client._transport = self._original_transport
            logger.debug("http_client_transport_restored")

        logger.info("raw_http_logger_plugin_shutdown_complete")

    async def validate(self) -> bool:
        """Validate plugin is ready."""
        # Plugin is always valid
        return True

    def get_routes(self) -> None:
        """Get plugin routes.

        Returns:
            None - this plugin doesn't expose any routes
        """
        return None

    async def health_check(self) -> HealthCheckResult:
        """Perform health check."""
        try:
            details = {
                "enabled": self._config.enabled if self._config else False,
            }

            if self._config and self._config.enabled:
                from pathlib import Path

                log_dir = (
                    self._config.log_dir
                    if isinstance(self._config.log_dir, Path)
                    else Path(self._config.log_dir)
                )
                details.update(
                    {
                        "log_dir": str(log_dir),
                        "log_dir_exists": log_dir.exists(),
                    }
                )

            return HealthCheckResult(
                status="pass",
                componentId=self.name,
                componentType="system_plugin",
                output="Raw HTTP logger is operational",
                version=self.version,
                details=details,
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
        """Raw HTTP logger plugin doesn't need scheduled tasks."""
        return None

    def get_config_class(self) -> type[BaseModel] | None:
        """Get configuration class."""
        return RawHTTPLoggerConfig

    def get_summary(self) -> dict[str, Any]:
        """Get plugin summary for logging."""
        summary = {
            "router_prefix": self.router_prefix,
            "enabled": self._config.enabled if self._config else False,
        }

        if self._config and self._config.enabled:
            from pathlib import Path

            log_dir = (
                self._config.log_dir
                if isinstance(self._config.log_dir, Path)
                else Path(self._config.log_dir)
            )
            summary.update(
                {
                    "log_dir": str(log_dir),
                    "log_client": f"req={self._config.log_client_request}, res={self._config.log_client_response}",
                    "log_provider": f"req={self._config.log_provider_request}, res={self._config.log_provider_response}",
                }
            )

        return summary
