"""HTTPX transport wrapper for tracing HTTP requests."""

import time
from collections.abc import AsyncIterator
from types import TracebackType
from typing import Any
from urllib.parse import urlparse

import httpx

from ccproxy.core.logging import get_plugin_logger
from ccproxy.observability import (
    ProviderRequestEvent,
    ProviderResponseEvent,
    get_observability_pipeline,
)
from ccproxy.observability.context import RequestContext

from .tracer import RequestTracerImpl


logger = get_plugin_logger()


class TracingResponseStream(httpx.AsyncByteStream):
    """Wraps response stream to log chunks without buffering."""

    def __init__(
        self,
        stream: Any,  # The actual httpx stream object
        tracer: RequestTracerImpl | None,
        request_id: str,
        status_code: int,
        headers: httpx.Headers,
        pipeline: Any = None,  # ObservabilityPipeline
        provider: str = "unknown",
        context: Any = None,  # RequestContext
        duration_ms: float = 0,  # Response duration
    ):
        self.stream = stream
        self.tracer = tracer
        self.request_id = request_id
        self.status_code = status_code
        self.headers = headers
        self._logged_headers = False
        self.pipeline = pipeline
        self.provider = provider
        self.context = context
        self.duration_ms = duration_ms
        self._body_chunks: list[bytes] = []  # Buffer for JSON logging

    async def __aiter__(self) -> AsyncIterator[bytes]:
        """Iterate over response chunks with logging."""
        logger.debug(
            "TracingResponseStream.__aiter__ started",
            request_id=self.request_id,
            status_code=self.status_code,
            provider=self.provider,
        )

        # Build and log response headers first (for raw logging)
        if not self._logged_headers and self.tracer and self.tracer.should_log_raw():
            header_list = [(k.encode(), v.encode()) for k, v in self.headers.items()]
            raw_headers = self.tracer.build_raw_response(self.status_code, header_list)
            await self.tracer.log_raw_provider_response(self.request_id, raw_headers)
            self._logged_headers = True

        # Stream and log chunks
        chunk_count = 0
        total_bytes = 0
        async for chunk in self.stream:
            chunk_count += 1
            chunk_size = len(chunk) if chunk else 0
            total_bytes += chunk_size

            logger.debug(
                "TracingResponseStream received chunk",
                request_id=self.request_id,
                chunk_number=chunk_count,
                chunk_size=chunk_size,
                total_bytes_so_far=total_bytes,
            )

            # Buffer chunk for JSON logging
            if chunk:
                self._body_chunks.append(chunk)

                # Log for raw HTTP tracing if enabled
                if self.tracer and self.tracer.should_log_raw():
                    await self.tracer.log_raw_provider_response(self.request_id, chunk)

            # Yield chunk unchanged (no buffering for the application)
            yield chunk

        # After all chunks are consumed, emit event with full body for JSON logging
        logger.debug(
            "TracingResponseStream finished streaming",
            request_id=self.request_id,
            total_chunks=chunk_count,
            total_bytes=total_bytes,
            has_pipeline=self.pipeline is not None,
        )

        if self.pipeline:
            full_body = b"".join(self._body_chunks) if self._body_chunks else None
            logger.debug(
                "TracingResponseStream emitting ProviderResponseEvent",
                request_id=self.request_id,
                body_size=len(full_body) if full_body else 0,
            )
            event = ProviderResponseEvent(
                request_id=self.request_id,
                provider=self.provider,
                status_code=self.status_code,
                headers=dict(self.headers),
                body=full_body,  # Include the full body!
                duration_ms=self.duration_ms,  # Include duration!
                context=self.context,
            )
            await self.pipeline.notify_provider_response(event)

    async def aclose(self) -> None:
        """Close the underlying stream."""
        if hasattr(self.stream, "aclose"):
            await self.stream.aclose()


class TracingHTTPTransport(httpx.AsyncHTTPTransport):
    """Wraps HTTPX transport to trace HTTP requests without buffering."""

    def __init__(
        self,
        wrapped_transport: httpx.AsyncHTTPTransport | None = None,
        tracer: RequestTracerImpl | None = None,
    ):
        self.wrapped = wrapped_transport or httpx.AsyncHTTPTransport()
        self.tracer = tracer
        self.pipeline = get_observability_pipeline()
        # Delegate pool to wrapped transport for context manager support
        if hasattr(self.wrapped, "_pool"):
            self._pool = self.wrapped._pool

        # Use module-level logger for initialization message
        from ccproxy.core.logging import get_plugin_logger

        module_logger = get_plugin_logger(__name__)
        module_logger.info(
            "TracingHTTPTransport initialized",
            enabled=self.tracer.enabled if self.tracer else False,
            category="middleware",
        )

    def _detect_provider(self, url: str) -> str:
        """Detect the AI provider from the URL."""
        try:
            parsed = urlparse(url)
            hostname = parsed.hostname
            if not hostname:
                return "unknown"

            # Map hostnames to provider names
            if "anthropic.com" in hostname:
                return "claude_api"
            elif "openai.com" in hostname or "api.openai.com" in hostname:
                return "openai"
            elif hostname.endswith("chatgpt.com"):
                return "codex"
            else:
                # Try to infer from URL path or hostname
                if "claude" in hostname.lower():
                    return "claude_sdk"
                elif "openai" in hostname.lower():
                    return "openai"
                else:
                    return "unknown"
        except Exception:
            return "unknown"

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Handle async request with raw logging and event emission."""
        # Extract request ID from extensions if available
        request_id = self._get_request_id(request)

        # Get current RequestContext
        context = RequestContext.get_current()

        # Detect provider from URL
        provider = self._detect_provider(str(request.url))

        # Track request start time
        request_start_time = time.time()

        # Emit provider request event if we have a request ID
        if request_id:
            request_event = ProviderRequestEvent(
                request_id=request_id,
                provider=provider,
                method=request.method,
                url=str(request.url),
                headers=dict(request.headers),  # Include headers!
                body=request.content,  # Include body!
                context=context,  # Pass the context!
            )
            await self.pipeline.notify_provider_request(request_event)

        # Log raw request if tracing is enabled
        if request_id and self.tracer and self.tracer.should_log_raw():
            raw_request = self._build_raw_request(request)
            await self.tracer.log_raw_provider_request(request_id, raw_request)

        # Forward request to wrapped transport
        response = await self.wrapped.handle_async_request(request)

        # Calculate response duration
        response_duration_ms = (time.time() - request_start_time) * 1000

        # Wrap response stream for logging - this handles both raw HTTP and JSON logging
        # Check if stream has required methods (duck typing)
        if (
            request_id
            and hasattr(response.stream, "__aiter__")
            and hasattr(response.stream, "aclose")
        ):
            logger.debug(
                "Wrapping response stream with TracingResponseStream",
                request_id=request_id,
                provider=provider,
                status_code=response.status_code,
                has_tracer=self.tracer is not None,
                should_log_raw=self.tracer.should_log_raw() if self.tracer else False,
            )
            response.stream = TracingResponseStream(
                response.stream,
                self.tracer if self.tracer and self.tracer.should_log_raw() else None,
                request_id,
                response.status_code,
                response.headers,
                pipeline=self.pipeline,  # Pass pipeline for JSON logging
                provider=provider,
                context=context,
                duration_ms=response_duration_ms,  # Pass duration for event
            )
            # DON'T emit event here - TracingResponseStream will emit it after consuming the body
        else:
            # For non-streaming responses or when stream doesn't support iteration,
            # emit the event here (without body since we can't read it without consuming)
            logger.debug(
                "Not wrapping stream - missing required methods",
                request_id=request_id,
                has_stream=hasattr(response, "stream"),
                has_aiter=hasattr(response.stream, "__aiter__")
                if hasattr(response, "stream")
                else False,
                has_aclose=hasattr(response.stream, "aclose")
                if hasattr(response, "stream")
                else False,
            )
            if request_id:
                response_event = ProviderResponseEvent(
                    request_id=request_id,
                    provider=provider,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    duration_ms=response_duration_ms,
                    context=context,
                    # Body cannot be captured here for non-streaming responses
                )
                await self.pipeline.notify_provider_response(response_event)

        return response

    def _get_request_id(self, request: httpx.Request) -> str | None:
        """Extract request ID from extensions if available."""
        # Try to get from extensions (passed by plugin_handler)
        if hasattr(request, "extensions") and "request_id" in request.extensions:
            return str(request.extensions["request_id"])

        # Log warning and return None - we won't log this request
        logger.warning(
            "provider_request_missing_request_id",
            url=str(request.url),
            method=request.method,
            message="Skipping raw HTTP logging for this request",
            category="middleware",
        )
        return None

    def _build_raw_request(self, request: httpx.Request) -> bytes:
        """Build raw HTTP/1.1 request format."""
        if not self.tracer:
            return b""

        # Convert httpx headers to list of tuples
        headers = [(k.encode(), v.encode()) for k, v in request.headers.items()]

        # Get request body
        body = None
        if request.content:
            body = request.content
        elif hasattr(request, "stream") and request.stream:
            # For streaming requests, we'd need to buffer which we want to avoid
            # Log a placeholder for now
            body = b"[STREAMING BODY - NOT CAPTURED]"

        return self.tracer.build_raw_request(
            method=request.method, url=str(request.url), headers=headers, body=body
        )

    async def aclose(self) -> None:
        """Close the transport."""
        if hasattr(self.wrapped, "aclose"):
            await self.wrapped.aclose()

    async def __aenter__(self) -> "TracingHTTPTransport":
        """Enter async context."""
        await self.wrapped.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_val: BaseException | None = None,
        exc_tb: TracebackType | None = None,
    ) -> None:
        """Exit async context."""
        await self.wrapped.__aexit__(exc_type, exc_val, exc_tb)
