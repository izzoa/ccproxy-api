import time
from typing import Any

import structlog

from ccproxy.observability import (
    ClientRequestEvent,
    ClientResponseEvent,
    ProviderRequestEvent,
    ProviderResponseEvent,
)

from .config import AccessLogConfig
from .formatter import AccessLogFormatter
from .writer import AccessLogWriter


logger = structlog.get_logger(__name__)


class AccessLogObserver:
    """Observer for access logging at client and provider levels.

    Integrates with the ObservabilityPipeline to receive events and log them
    according to configuration settings.
    """

    def __init__(self, config: AccessLogConfig):
        self.config = config
        self.formatter = AccessLogFormatter()

        # Create writers based on configuration
        self.client_writer: AccessLogWriter | None = None
        self.provider_writer: AccessLogWriter | None = None

        if config.client_enabled:
            self.client_writer = AccessLogWriter(
                config.client_log_file,
                config.buffer_size,
                config.flush_interval,
            )

        if config.provider_enabled:
            self.provider_writer = AccessLogWriter(
                config.provider_log_file,
                config.buffer_size,
                config.flush_interval,
            )

        # Track in-flight requests
        self.client_requests: dict[str, dict[str, Any]] = {}
        self.provider_requests: dict[str, dict[str, Any]] = {}

        logger.info(
            "access_log_observer_initialized",
            client_enabled=config.client_enabled,
            client_format=config.client_format,
            provider_enabled=config.provider_enabled,
        )

    # Client-level events

    async def on_client_request(self, event: ClientRequestEvent) -> None:
        """Track incoming client request."""
        if not self.config.client_enabled:
            return

        # Check if path should be logged
        if not self._should_log(event.path):
            return

        # Store request data for later
        self.client_requests[event.request_id] = {
            "timestamp": event.timestamp,
            "method": event.method,
            "path": event.path,
            "query": event.query,
            "client_ip": event.client_ip,
            "user_agent": event.user_agent,
            "start_time": time.time(),
        }

    async def on_client_response(self, event: ClientResponseEvent) -> None:
        """Log completed client request."""
        if not self.config.client_enabled:
            return

        # Check if we have the request data
        if event.request_id not in self.client_requests:
            return

        # Get and remove request data
        request_data = self.client_requests.pop(event.request_id)

        # Calculate duration
        duration_ms = (time.time() - request_data["start_time"]) * 1000

        # Merge request and response data
        log_data = {
            **request_data,
            "request_id": event.request_id,
            "status_code": event.status_code,
            "body_size": event.body_size,
            "duration_ms": duration_ms,
            "error": event.error,
        }

        # Format and write
        if self.client_writer:
            formatted = self.formatter.format_client(
                log_data, self.config.client_format
            )
            await self.client_writer.write(formatted)

    # Provider-level events

    async def on_provider_request(self, event: ProviderRequestEvent) -> None:
        """Track outgoing provider request."""
        if not self.config.provider_enabled:
            return

        # Store request data for later
        self.provider_requests[event.request_id] = {
            "timestamp": event.timestamp,
            "provider": event.provider,
            "method": event.method,
            "url": event.url,
            "start_time": time.time(),
        }

    async def on_provider_response(self, event: ProviderResponseEvent) -> None:
        """Log completed provider request."""
        if not self.config.provider_enabled:
            return

        # Check if we have the request data
        if event.request_id not in self.provider_requests:
            return

        # Get and remove request data
        request_data = self.provider_requests.pop(event.request_id)

        # Calculate duration if not provided
        duration_ms = event.duration_ms
        if duration_ms == 0:
            duration_ms = (time.time() - request_data["start_time"]) * 1000

        # Merge request and response data
        log_data = {
            **request_data,
            "request_id": event.request_id,
            "status_code": event.status_code,
            "duration_ms": duration_ms,
            "tokens_input": event.tokens_input,
            "tokens_output": event.tokens_output,
            "cache_read_tokens": event.cache_read_tokens,
            "cache_write_tokens": event.cache_write_tokens,
            "cost_usd": event.cost_usd,
            "model": event.model,
        }

        # Format and write
        if self.provider_writer:
            formatted = self.formatter.format_provider(log_data)
            await self.provider_writer.write(formatted)

    def _should_log(self, path: str) -> bool:
        """Check if a path should be logged based on exclusion rules.

        Args:
            path: The request path

        Returns:
            True if the path should be logged, False otherwise
        """
        for excluded in self.config.exclude_paths:
            if path.startswith(excluded):
                return False
        return True

    async def close(self) -> None:
        """Close writers and flush any pending data."""
        if self.client_writer:
            await self.client_writer.close()
        if self.provider_writer:
            await self.provider_writer.close()
