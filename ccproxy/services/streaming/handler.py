"""Streaming request handler for SSE and chunked responses."""

import json
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

import httpx
import structlog
from fastapi.responses import StreamingResponse

from ccproxy.adapters.base import APIAdapter
from ccproxy.observability.context import RequestContext
from ccproxy.observability.metrics import PrometheusMetrics
from ccproxy.services.provider_context import ProviderContext


logger = structlog.get_logger(__name__)


class StreamingHandler:
    """Manages streaming request processing and SSE adaptation."""

    def __init__(
        self,
        metrics: PrometheusMetrics | None = None,
        verbose_streaming: bool = False,
    ) -> None:
        """Initialize with metrics collector and debug settings.

        - Sets up Prometheus metrics if provided
        - Configures verbose streaming from environment
        """
        self.metrics = metrics
        self.verbose_streaming = verbose_streaming

    def should_stream_response(self, headers: dict[str, str]) -> bool:
        """Check Accept header for streaming indicators.

        - Looks for 'text/event-stream' in Accept header
        - Also checks for generic 'stream' indicator
        - Case-insensitive comparison
        """
        accept_header = headers.get("accept", "").lower()
        return "text/event-stream" in accept_header or "stream" in accept_header

    async def should_stream(
        self, request_body: bytes, provider_context: ProviderContext
    ) -> bool:
        """Check if request body has stream:true flag.

        - Returns False if provider doesn't support streaming
        - Parses JSON body for 'stream' field
        - Handles parse errors gracefully
        """
        if not provider_context.supports_streaming:
            return False

        try:
            data = json.loads(request_body)
            return data.get("stream", False) is True
        except (json.JSONDecodeError, TypeError):
            return False

    async def handle_streaming_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
        provider_context: ProviderContext,
        request_context: RequestContext,
        client_config: dict[str, Any] | None = None,
    ) -> StreamingResponse:
        """Execute streaming HTTP request with SSE processing.

        - Creates async client with proper timeout
        - Processes SSE events with adapter if provided
        - Returns StreamingResponseWithLogging wrapper
        """

        async def stream_generator():
            """Generate streaming response chunks."""
            total_chunks = 0
            total_bytes = 0

            try:
                # Create HTTP client with appropriate config
                config = client_config or {}
                async with httpx.AsyncClient(**config) as client:
                    # Make streaming request
                    async with client.stream(
                        method=method,
                        url=url,
                        headers=headers,
                        content=body,
                        timeout=httpx.Timeout(300.0),  # 5 minute timeout for streaming
                    ) as response:
                        # Check for error status
                        if response.status_code >= 400:
                            error_body = await response.aread()
                            yield error_body
                            return

                        # Stream the response
                        if provider_context.response_adapter:
                            # Process SSE events with adapter
                            async for chunk in self._process_sse_events(
                                response, provider_context.response_adapter
                            ):
                                total_chunks += 1
                                total_bytes += len(chunk)
                                yield chunk
                        else:
                            # Pass through raw chunks
                            async for chunk in response.aiter_bytes():
                                total_chunks += 1
                                total_bytes += len(chunk)
                                yield chunk

                # Update metrics
                if request_context:
                    request_context.metrics["stream_chunks"] = total_chunks
                    request_context.metrics["stream_bytes"] = total_bytes

            except httpx.TimeoutException:
                logger.error("Streaming request timeout", url=url)
                error_msg = json.dumps({"error": "Request timeout"}).encode()
                yield error_msg
            except Exception as e:
                logger.error("Streaming request failed", url=url, error=str(e))
                error_msg = json.dumps({"error": str(e)}).encode()
                yield error_msg

        # Return streaming response
        return StreamingResponseWithLogging(
            stream_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Request-ID": request_context.request_id if request_context else "",
                "X-Accel-Buffering": "no",  # Disable nginx buffering
            },
            request_context=request_context,
            metrics=self.metrics,
        )

    async def _process_sse_events(
        self, response: httpx.Response, adapter: APIAdapter
    ) -> AsyncGenerator[bytes, None]:
        """Parse and adapt SSE events from response stream.

        - Maintains buffer for incomplete events
        - Splits on double newline boundaries
        - Applies adapter transformation to each event
        - Handles [DONE] marker specially
        """
        buffer = b""

        async for chunk in response.aiter_bytes():
            buffer += chunk

            # Process complete events (separated by double newline)
            while b"\n\n" in buffer:
                event_end = buffer.index(b"\n\n")
                event_data = buffer[:event_end]
                buffer = buffer[event_end + 2 :]

                # Skip empty events
                if not event_data:
                    continue

                # Check for [DONE] marker
                if event_data == b"data: [DONE]":
                    yield b"data: [DONE]\n\n"
                    continue

                # Parse and adapt event
                adapted_event = await self._adapt_sse_event(event_data, adapter)
                if adapted_event:
                    yield adapted_event + b"\n\n"

        # Process any remaining data in buffer
        if buffer and buffer != b"\n":
            adapted_event = await self._adapt_sse_event(buffer, adapter)
            if adapted_event:
                yield adapted_event + b"\n\n"

    async def _adapt_sse_event(
        self, event_bytes: bytes, adapter: APIAdapter
    ) -> bytes | None:
        """Adapt a single SSE event using the adapter."""
        try:
            # Extract JSON from SSE event
            event_str = event_bytes.decode("utf-8")
            if not event_str.startswith("data: "):
                return event_bytes  # Pass through non-data events

            json_str = event_str[6:]  # Remove "data: " prefix
            if json_str == "[DONE]":
                return event_bytes

            # Parse JSON and adapt
            try:
                event_data = json.loads(json_str)
                adapted_data = adapter.adapt_response(event_data)
                adapted_json = json.dumps(adapted_data)
                return f"data: {adapted_json}".encode()
            except json.JSONDecodeError:
                # Pass through malformed events
                return event_bytes

        except Exception as e:
            logger.error("Failed to adapt SSE event", error=str(e))
            return event_bytes


class StreamingResponseWithLogging(StreamingResponse):
    """Streaming response wrapper that logs completion."""

    def __init__(
        self,
        content: AsyncIterator[bytes],
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        media_type: str | None = None,
        request_context: RequestContext | None = None,
        metrics: PrometheusMetrics | None = None,
    ):
        super().__init__(content, status_code, headers, media_type)
        self.request_context = request_context
        self.metrics = metrics

    async def __call__(self, scope, receive, send) -> None:
        """Override to log streaming completion."""
        try:
            await super().__call__(scope, receive, send)
        finally:
            if self.request_context:
                logger.info(
                    "Streaming response complete",
                    request_id=self.request_context.request_id,
                    metrics=self.request_context.metrics,
                )

                # Update Prometheus metrics if available
                if self.metrics:
                    self.metrics.record_streaming_complete(
                        self.request_context.provider,
                        self.request_context.metrics.get("stream_chunks", 0),
                        self.request_context.metrics.get("stream_bytes", 0),
                    )
