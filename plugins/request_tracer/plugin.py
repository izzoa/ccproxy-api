"""Request Tracer plugin implementation."""

from typing import Any

from ccproxy.core.logging import get_plugin_logger
from ccproxy.observability import get_observability_pipeline
from ccproxy.plugins import (
    MiddlewareLayer,
    MiddlewareSpec,
    PluginContext,
    PluginManifest,
    SystemPluginFactory,
    SystemPluginRuntime,
)

from .config import RequestTracerConfig
from .middleware import RequestTracingMiddleware
from .observer import TracerObserver
from .tracer import RequestTracerImpl
from .transport import TracingHTTPTransport


logger = get_plugin_logger()


class RequestTracerRuntime(SystemPluginRuntime):
    """Runtime for request tracer plugin."""

    def __init__(self, manifest: PluginManifest):
        """Initialize runtime."""
        super().__init__(manifest)
        self.config: RequestTracerConfig | None = None
        self.tracer_instance: RequestTracerImpl | None = None
        self.observer: TracerObserver | None = None
        self.original_transport: Any | None = None

    async def _on_initialize(self) -> None:
        """Initialize the request tracer."""
        if not self.context:
            raise RuntimeError("Context not set")

        # Get configuration
        config = self.context.get("config")
        if not isinstance(config, RequestTracerConfig):
            logger.warning("plugin_no_config")
            # Use default config if none provided
            config = RequestTracerConfig()
            logger.info("plugin_using_default_config")
        self.config = config

        # Create tracer instance (for backward compatibility)
        self.tracer_instance = RequestTracerImpl(self.config)

        # Create observer for the new pipeline
        self.observer = TracerObserver(self.config)

        if self.config.enabled:
            # Register observer with ObservabilityPipeline
            pipeline = get_observability_pipeline()
            pipeline.register_observer(self.observer)
            logger.info(
                "tracer_observer_registered_with_pipeline",
                observer_count=pipeline.get_observer_count(),
            )

            # Register tracer with service container (for backward compatibility)
            service_container = self.context.get("service_container")
            if service_container and hasattr(service_container, "set_request_tracer"):
                # Use the public method to set the tracer
                service_container.set_request_tracer(self.tracer_instance)
                logger.info(
                    "request_tracer_registered",
                    verbose_api=self.config.verbose_api,
                    raw_http=self.config.raw_http_enabled,
                )

            # Wrap HTTP client transport for provider logging if raw HTTP is enabled
            if self.config.raw_http_enabled:
                await self._wrap_http_client_transport()

            logger.info(
                "request_tracer_enabled",
                log_dir=self.config.log_dir,
                verbose_api=self.config.verbose_api,
                json_logs=self.config.json_logs_enabled,
                raw_http=self.config.raw_http_enabled,
                log_client_request=self.config.log_client_request,
                log_client_response=self.config.log_client_response,
                log_provider_request=self.config.log_provider_request,
                log_provider_response=self.config.log_provider_response,
                max_body_size=self.config.max_body_size,
                exclude_paths=self.config.exclude_paths,
                exclude_headers=self.config.exclude_headers,
            )
        else:
            logger.info("request_tracer_disabled")

    async def _wrap_http_client_transport(self) -> None:
        """Wrap the shared HTTP client's transport with tracing."""
        if not self.context:
            return

        http_client = self.context.get("http_client")
        if not http_client:
            logger.warning("no_http_client_to_wrap")
            return

        # Get the current transport
        current_transport = http_client._transport

        # Only wrap if not already wrapped
        if not isinstance(current_transport, TracingHTTPTransport):
            # Store original for potential unwrapping
            self.original_transport = current_transport

            # Create and set tracing transport
            from httpx import AsyncHTTPTransport

            wrapped = (
                current_transport
                if isinstance(current_transport, AsyncHTTPTransport)
                else None
            )
            tracing_transport = TracingHTTPTransport(
                wrapped_transport=wrapped, tracer=self.tracer_instance
            )
            http_client._transport = tracing_transport

            logger.debug("http_client_transport_wrapped", category="middleware")

    async def _on_shutdown(self) -> None:
        """Cleanup on shutdown."""
        # Unregister observer from pipeline
        if self.observer:
            pipeline = get_observability_pipeline()
            pipeline.unregister_observer(self.observer)
            logger.debug("tracer_observer_unregistered", category="middleware")

        # Restore original transport if we wrapped it
        if self.context and self.original_transport:
            http_client = self.context.get("http_client")
            if http_client:
                http_client._transport = self.original_transport
                logger.debug("http_client_transport_restored", category="middleware")

        # Restore null tracer on shutdown
        if self.context:
            service_container = self.context.get("service_container")
            if service_container and hasattr(service_container, "set_request_tracer"):
                from ccproxy.services.tracing import NullRequestTracer

                service_container.set_request_tracer(NullRequestTracer())
                logger.debug("restored_null_tracer", category="middleware")

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
                    "verbose_api": self.config.verbose_api,
                    "json_logs": self.config.json_logs_enabled,
                    "raw_http": self.config.raw_http_enabled,
                }
            )

        return details


class RequestTracerFactory(SystemPluginFactory):
    """Factory for request tracer plugin."""

    def __init__(self) -> None:
        """Initialize factory with manifest."""
        # Create manifest with static declarations
        manifest = PluginManifest(
            name="request_tracer",
            version="2.0.0",
            description="Unified request tracer with structured JSON and raw HTTP logging",
            is_provider=False,
            config_class=RequestTracerConfig,
        )

        # Initialize with manifest and runtime class
        super().__init__(manifest)

        # Store reference to tracer instance for middleware creation
        self._tracer_instance: RequestTracerImpl | None = None

    def create_runtime(self) -> RequestTracerRuntime:
        """Create runtime instance."""
        return RequestTracerRuntime(self.manifest)

    def create_context(self, core_services: Any) -> PluginContext:
        """Create context and update manifest with middleware if enabled."""
        # Get base context
        context = super().create_context(core_services)

        # Check if plugin is enabled and raw HTTP logging is enabled
        config = context.get("config")
        if (
            isinstance(config, RequestTracerConfig)
            and config.enabled
            and config.raw_http_enabled
        ):
            # Check if compression is enabled and warn the user
            from ccproxy.config import get_settings

            settings = get_settings()
            if (
                settings
                and hasattr(settings, "http")
                and settings.http.compression_enabled
            ):
                logger.warning(
                    "request_tracer_with_compression_warning",
                    message="Request tracer is enabled with HTTP compression. Response bodies may be compressed/unreadable in JSON logs.",
                    recommendation="Set HTTP__COMPRESSION_ENABLED=false for readable response bodies",
                    compression_enabled=settings.http.compression_enabled,
                    accept_encoding=settings.http.accept_encoding,
                )

            # Create tracer instance for middleware
            self._tracer_instance = RequestTracerImpl(config)

            # Add middleware to manifest
            # This is safe because it happens during app creation phase
            if not self.manifest.middleware:
                self.manifest.middleware = []

            # Create middleware spec with proper configuration
            middleware_spec = MiddlewareSpec(
                middleware_class=RequestTracingMiddleware,  # type: ignore[arg-type]
                priority=MiddlewareLayer.OBSERVABILITY
                - 10,  # Early in observability layer
                kwargs={"tracer": self._tracer_instance},
            )

            self.manifest.middleware.append(middleware_spec)

        return context


# Export the factory instance
factory = RequestTracerFactory()
