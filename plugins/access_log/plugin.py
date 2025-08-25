from typing import Any

from ccproxy.core.logging import get_plugin_logger
from ccproxy.observability import get_observability_pipeline
from ccproxy.plugins import (
    PluginManifest,
    SystemPluginFactory,
    SystemPluginRuntime,
)

from .config import AccessLogConfig
from .observer import AccessLogObserver


logger = get_plugin_logger()


class AccessLogRuntime(SystemPluginRuntime):
    """Runtime for access log plugin.

    Integrates with the ObservabilityPipeline to receive and log events.
    """

    def __init__(self, manifest: PluginManifest):
        super().__init__(manifest)
        self.observer: AccessLogObserver | None = None
        self.config: AccessLogConfig | None = None

    async def _on_initialize(self) -> None:
        """Initialize the access logger."""
        if not self.context:
            raise RuntimeError("Context not set")

        # Get configuration
        config = self.context.get("config")
        if not isinstance(config, AccessLogConfig):
            config = AccessLogConfig()
        self.config = config

        if not config.enabled:
            logger.info("access_log_disabled")
            return

        # Create observer
        self.observer = AccessLogObserver(config)

        # Register with ObservabilityPipeline
        pipeline = get_observability_pipeline()
        pipeline.register_observer(self.observer)

        logger.info(
            "access_log_enabled",
            client_enabled=config.client_enabled,
            client_format=config.client_format,
            client_log_file=config.client_log_file,
            provider_enabled=config.provider_enabled,
            provider_log_file=config.provider_log_file,
            observers_count=pipeline.get_observer_count(),
            note="Integrated with ObservabilityPipeline",
        )

    async def _on_shutdown(self) -> None:
        """Cleanup on shutdown."""
        if self.observer:
            # Unregister from pipeline
            pipeline = get_observability_pipeline()
            pipeline.unregister_observer(self.observer)

            # Close observer
            await self.observer.close()
            logger.debug("access_log_shutdown")

    async def _get_health_details(self) -> dict[str, Any]:
        """Get health check details."""
        config = self.config

        return {
            "type": "system",
            "initialized": self.initialized,
            "enabled": config.enabled if config else False,
            "client_enabled": config.client_enabled if config else False,
            "provider_enabled": config.provider_enabled if config else False,
            "mode": "pipeline",  # Now integrated with ObservabilityPipeline
        }

    def get_observer(self) -> AccessLogObserver | None:
        """Get the observer instance (for testing or manual integration)."""
        return self.observer


class AccessLogFactory(SystemPluginFactory):
    """Factory for access log plugin."""

    def __init__(self) -> None:
        manifest = PluginManifest(
            name="access_log",
            version="1.0.0",
            description="Simple access logging with Common, Combined, and Structured formats",
            is_provider=False,
            config_class=AccessLogConfig,
        )
        super().__init__(manifest)

    def create_runtime(self) -> AccessLogRuntime:
        return AccessLogRuntime(self.manifest)


# Export the factory instance
factory = AccessLogFactory()
