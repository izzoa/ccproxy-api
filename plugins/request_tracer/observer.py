"""Observer implementation for request tracer plugin."""

from ccproxy.core.logging import get_plugin_logger
from ccproxy.observability import (
    ClientRequestEvent,
    ClientResponseEvent,
    ProviderRequestEvent,
    ProviderResponseEvent,
    RequestObserver,
)

from .config import RequestTracerConfig
from .formatters import JSONFormatter, RawHTTPFormatter


logger = get_plugin_logger()


class TracerObserver(RequestObserver):
    """Observer for request tracing at both client and provider levels.

    This observer handles both structured JSON logging and raw HTTP
    protocol logging based on configuration.
    """

    def __init__(self, config: RequestTracerConfig):
        """Initialize the tracer observer.

        Args:
            config: Request tracer configuration
        """
        self.config = config
        self.json_formatter = (
            JSONFormatter(config) if config.json_logs_enabled else None
        )
        self.raw_formatter = (
            RawHTTPFormatter(config) if config.raw_http_enabled else None
        )

        logger.info(
            "tracer_observer_initialized",
            json_enabled=config.json_logs_enabled,
            raw_enabled=config.raw_http_enabled,
            log_client=config.log_client_request,
            log_provider=config.log_provider_request,
        )

    # Client-level events

    async def on_client_request(self, event: ClientRequestEvent) -> None:
        """Log incoming client request.

        Args:
            event: Client request event
        """
        if not self.config.log_client_request:
            return

        # Check if path should be traced
        if not self.config.should_trace_path(event.path):
            return

        if self.json_formatter:
            # Delegate to JSON formatter for structured logging
            await self.json_formatter.log_request(
                request_id=event.request_id,
                method=event.method,
                url=f"http://localhost{event.path}{'?' + event.query if event.query else ''}",
                headers=event.headers or {},
                body=event.body,
                request_type="client",  # Specify this is a client request
                context=event.context,  # Pass the full context
            )

    async def on_client_response(self, event: ClientResponseEvent) -> None:
        """Log client response.

        Args:
            event: Client response event
        """
        if not self.config.log_client_response:
            return

        if self.json_formatter:
            # Delegate to JSON formatter for structured logging
            await self.json_formatter.log_response(
                request_id=event.request_id,
                status=event.status_code,
                headers=event.headers or {},
                body=event.body or b"",  # Client response body if available
                response_type="client",  # Specify this is a client response
                context=event.context,  # Pass the full context
            )

    # Provider-level events

    async def on_provider_request(self, event: ProviderRequestEvent) -> None:
        """Log outgoing provider request.

        Args:
            event: Provider request event
        """
        if not self.config.log_provider_request:
            return

        if self.json_formatter:
            # Delegate to JSON formatter for structured logging
            await self.json_formatter.log_request(
                request_id=event.request_id,
                method=event.method,
                url=event.url,
                headers=event.headers or {},
                body=event.body,
                request_type="provider",  # Specify this is a provider request
                context=event.context,  # Pass the full context
            )

    async def on_provider_response(self, event: ProviderResponseEvent) -> None:
        """Log provider response.

        Args:
            event: Provider response event
        """
        if not self.config.log_provider_response:
            return

        logger.debug(
            "on_provider_response called",
            request_id=event.request_id,
            status_code=event.status_code,
            has_body=event.body is not None,
            body_size=len(event.body) if event.body else 0,
            provider=event.provider,
        )

        if self.json_formatter:
            # Delegate to JSON formatter for structured logging
            # For now, just pass the basic parameters
            # TODO: Extend JSONFormatter to handle metadata
            await self.json_formatter.log_response(
                request_id=event.request_id,
                status=event.status_code,
                headers=event.headers or {},
                body=event.body or b"",
                response_type="provider",  # Specify this is a provider response
                context=event.context,  # Pass the full context
            )
