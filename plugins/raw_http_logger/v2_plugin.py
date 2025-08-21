"""Raw HTTP Logger plugin v2 implementation."""

from typing import Any

import structlog

from ccproxy.plugins.v2 import (
    MiddlewareLayer,
    MiddlewareSpec,
    PluginContext,
    PluginManifest,
    SystemPluginFactory,
    SystemPluginRuntime,
)

from .config import RawHTTPLoggerConfig
from .logger import RawHTTPLogger
from .middleware import RawHTTPLoggingMiddleware
from .transport import LoggingHTTPTransport


logger = structlog.get_logger(__name__)


class RawHTTPLoggerRuntime(SystemPluginRuntime):
    """Runtime for raw HTTP logger plugin."""

    def __init__(self, manifest: PluginManifest):
        """Initialize runtime."""
        super().__init__(manifest)
        self.config: RawHTTPLoggerConfig | None = None
        self.logger_instance: RawHTTPLogger | None = None
        self.original_transport: Any | None = None

    async def _on_initialize(self) -> None:
        """Initialize the raw HTTP logger."""
        if not self.context:
            raise RuntimeError("Context not set")

        # Get configuration
        self.config = self.context.get("config")
        if not self.config:
            logger.warning("raw_http_logger_no_config", plugin=self.name)
            return

        # Create logger instance
        self.logger_instance = RawHTTPLogger(self.config)  # type: ignore[no-untyped-call]

        if self.config.enabled:
            # Wrap HTTP client transport for provider logging
            await self._wrap_http_client_transport()

            logger.info(
                "raw_http_logger_enabled",
                log_dir=self.config.log_dir,
                log_client_request=self.config.log_client_request,
                log_client_response=self.config.log_client_response,
                log_provider_request=self.config.log_provider_request,
                log_provider_response=self.config.log_provider_response,
                max_body_size=self.config.max_body_size,
                exclude_paths=self.config.exclude_paths,
                exclude_headers=self.config.exclude_headers,
            )
        else:
            logger.info("raw_http_logger_disabled")

    async def _wrap_http_client_transport(self) -> None:
        """Wrap the shared HTTP client's transport with logging."""
        if not self.context:
            return

        http_client = self.context.get("http_client")
        if not http_client:
            logger.warning("no_http_client_to_wrap")
            return

        # Get the current transport
        current_transport = http_client._transport

        # Only wrap if not already wrapped
        if not isinstance(current_transport, LoggingHTTPTransport):
            # Store original for potential unwrapping
            self.original_transport = current_transport

            # Create and set logging transport
            logging_transport = LoggingHTTPTransport(
                wrapped_transport=current_transport, logger=self.logger_instance
            )
            http_client._transport = logging_transport

            logger.debug("http_client_transport_wrapped")

    async def _on_shutdown(self) -> None:
        """Cleanup on shutdown."""
        # Restore original transport if we wrapped it
        if self.context and self.original_transport:
            http_client = self.context.get("http_client")
            if http_client:
                http_client._transport = self.original_transport
                logger.debug("http_client_transport_restored")

    async def _get_health_details(self) -> dict[str, Any]:
        """Get health check details."""
        details = {
            "type": "system",
            "initialized": self.initialized,
            "enabled": self.config.enabled if self.config else False,
        }

        if self.config and self.config.enabled:
            from pathlib import Path

            log_dir = Path(self.config.log_dir)
            details.update(
                {
                    "log_dir": str(log_dir),
                    "log_dir_exists": log_dir.exists(),
                }
            )

        return details


class RawHTTPLoggerFactory(SystemPluginFactory):
    """Factory for raw HTTP logger plugin."""

    def __init__(self) -> None:
        """Initialize factory with manifest."""
        # Create manifest with static declarations
        manifest = PluginManifest(
            name="raw_http_logger",
            version="1.0.0",
            description="Raw HTTP Logger plugin for debugging HTTP requests and responses",
            is_provider=False,
            config_class=RawHTTPLoggerConfig,
        )

        # Initialize with manifest and runtime class
        super().__init__(manifest)

        # Store reference to logger instance for middleware creation
        self._logger_instance: RawHTTPLogger | None = None

    def create_runtime(self) -> RawHTTPLoggerRuntime:
        """Create runtime instance."""
        return RawHTTPLoggerRuntime(self.manifest)

    def create_context(self, core_services: Any) -> PluginContext:
        """Create context and update manifest with middleware if enabled."""
        # Get base context
        context = super().create_context(core_services)

        # Check if plugin is enabled
        config = context.get("config")
        if config and config.enabled:
            # Create logger instance for middleware
            self._logger_instance = RawHTTPLogger(config)  # type: ignore[no-untyped-call]

            # Add middleware to manifest
            # This is safe because it happens during app creation phase
            if not self.manifest.middleware:
                self.manifest.middleware = []

            # Create middleware spec with proper configuration
            middleware_spec = MiddlewareSpec(
                middleware_class=RawHTTPLoggingMiddleware,  # type: ignore[arg-type]
                priority=MiddlewareLayer.OBSERVABILITY
                - 10,  # Early in observability layer
                kwargs={"logger": self._logger_instance},
            )

            self.manifest.middleware.append(middleware_spec)
            logger.debug("raw_http_logger_middleware_added_to_manifest")

        return context


# Export the factory instance
factory = RawHTTPLoggerFactory()
