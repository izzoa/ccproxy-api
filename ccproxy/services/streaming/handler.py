"""Streaming request handler for SSE and chunked responses."""

import json
from collections.abc import AsyncGenerator, AsyncIterator, MutableMapping
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

        async def stream_generator() -> AsyncGenerator[bytes, None]:
            """Generate streaming response chunks."""
            total_chunks = 0
            total_bytes = 0

            try:
                # Create HTTP client with appropriate config
                config = client_config or {}
                async with (
                    httpx.AsyncClient(**config) as client,
                    client.stream(
                        method=method,
                        url=url,
                        headers=headers,
                        content=body,
                        timeout=httpx.Timeout(300.0),  # 5 minute timeout for streaming
                    ) as response,
                ):
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

                # Update metrics if available
                if request_context and hasattr(request_context, "metrics"):
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

    async def _parse_sse_to_json_stream(
        self, raw_stream: AsyncIterator[bytes]
    ) -> AsyncIterator[dict[str, Any]]:
        """Parse raw SSE bytes stream into JSON chunks.

        Yields JSON objects extracted from SSE events without buffering
        the entire response.
        """
        buffer = b""

        async for chunk_bytes in raw_stream:
            buffer += chunk_bytes

            # Process complete SSE events in buffer
            while b"\n\n" in buffer:
                # Split at the first complete event
                event_bytes, buffer = buffer.split(b"\n\n", 1)

                try:
                    # Decode the complete event
                    event_str = event_bytes.decode("utf-8")

                    # Skip empty events
                    if not event_str.strip():
                        continue

                    # Check if this is a data event
                    if "data: " in event_str:
                        # Extract data from the event
                        for line in event_str.split("\n"):
                            if line.startswith("data: "):
                                data_str = line[6:].strip()
                                if data_str == "[DONE]":
                                    # Don't yield DONE marker as JSON
                                    continue
                                else:
                                    try:
                                        chunk_json = json.loads(data_str)
                                        yield chunk_json
                                    except json.JSONDecodeError:
                                        # Skip invalid JSON
                                        logger.warning(
                                            "Failed to parse SSE data as JSON",
                                            data=data_str[:100],
                                        )
                                        continue
                except Exception as e:
                    logger.warning("Failed to process SSE event", error=str(e))
                    continue

    async def _serialize_json_to_sse_stream(
        self, json_stream: AsyncIterator[dict[str, Any]]
    ) -> AsyncIterator[bytes]:
        """Serialize JSON chunks back to SSE format.

        Converts adapted JSON objects back into SSE events.
        """
        async for chunk in json_stream:
            try:
                # Serialize JSON chunk to SSE format
                json_str = json.dumps(chunk, separators=(",", ":"))
                sse_event = f"data: {json_str}\n\n"
                yield sse_event.encode()
            except Exception as e:
                logger.warning(
                    "Failed to serialize JSON to SSE",
                    error=str(e),
                    chunk_type=type(chunk).__name__,
                )
                continue

        # Send final DONE marker
        yield b"data: [DONE]\n\n"

    async def _process_sse_events(
        self, response: httpx.Response, adapter: APIAdapter
    ) -> AsyncGenerator[bytes, None]:
        """Parse and adapt SSE events from response stream using new pipeline.

        - Parse raw SSE bytes to JSON chunks
        - Pass entire JSON stream through adapter (maintains state)
        - Serialize adapted chunks back to SSE format
        """
        # Create streaming pipeline:
        # 1. Parse raw SSE bytes to JSON chunks
        json_stream = self._parse_sse_to_json_stream(response.aiter_bytes())

        # 2. Pass entire JSON stream through adapter (maintains state)
        adapted_stream = adapter.adapt_stream(json_stream)

        # 3. Serialize adapted chunks back to SSE format
        async for sse_bytes in self._serialize_json_to_sse_stream(adapted_stream):  # type: ignore[arg-type]
            yield sse_bytes


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

    async def __call__(
        self, scope: MutableMapping[str, Any], receive: Any, send: Any
    ) -> None:
        """Override to log streaming completion."""
        try:
            await super().__call__(scope, receive, send)
        finally:
            if self.request_context:
                # Log completion with available metrics
                log_metrics = {}
                if hasattr(self.request_context, "metrics"):
                    log_metrics = self.request_context.metrics

                logger.info(
                    "Streaming response complete",
                    request_id=self.request_context.request_id,
                    metrics=log_metrics,
                )

                # Update Prometheus metrics if available and context has needed attributes
                if (
                    self.metrics
                    and hasattr(self.request_context, "provider")
                    and hasattr(self.request_context, "metrics")
                ):
                    # Record streaming completion metrics using available methods
                    provider = getattr(self.request_context, "provider", "unknown")
                    stream_chunks = self.request_context.metrics.get("stream_chunks", 0)
                    stream_bytes = self.request_context.metrics.get("stream_bytes", 0)

                    # Use existing methods to record streaming metrics
                    self.metrics.record_request(
                        method="STREAM",
                        endpoint="streaming",
                        model=getattr(self.request_context, "model", None),
                        status="200",
                        service_type=provider,
                    )
