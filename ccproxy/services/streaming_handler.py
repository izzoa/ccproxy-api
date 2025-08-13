"""Streaming response handler for proxy requests."""

import contextlib
import json
import time
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

import httpx
import structlog
from fastapi.responses import StreamingResponse

from ccproxy.observability.streaming_response import StreamingResponseWithLogging
from ccproxy.utils.simple_request_logger import append_streaming_log, write_request_log
from ccproxy.utils.streaming_metrics import StreamingMetricsCollector


if TYPE_CHECKING:
    from ccproxy.observability.context import RequestContext
    from ccproxy.services.proxy_service import RequestData


logger = structlog.get_logger(__name__)


class StreamingHandler:
    """Handle streaming request processing and response transformation."""

    def __init__(
        self,
        proxy_url: str | None = None,
        ssl_context: str | bool = True,
        verbose_streaming: bool = False,
        verbose_api: bool = False,
    ):
        self.proxy_url = proxy_url
        self.ssl_context = ssl_context
        self.verbose_streaming = verbose_streaming
        self.verbose_api = verbose_api

    async def handle_streaming_request(
        self,
        request_data: "RequestData",
        original_path: str,
        timeout: float,
        ctx: "RequestContext",
        response_transformer: Any,
        openai_adapter: Any,
        proxy_mode: str,
        metrics: Any,
    ) -> StreamingResponse | tuple[int, dict[str, str], bytes]:
        """Handle streaming request with transformation.

        Args:
            request_data: Transformed request data
            original_path: Original request path for context
            timeout: Request timeout
            ctx: Request context for observability
            response_transformer: Response transformer instance
            openai_adapter: OpenAI adapter instance
            proxy_mode: Proxy transformation mode
            metrics: Metrics collector

        Returns:
            StreamingResponse or error response tuple
        """
        # Log the outgoing request if verbose API logging is enabled
        await self._log_verbose_api_request(request_data, ctx)

        # First, make the request and check for errors before streaming
        async with httpx.AsyncClient(
            timeout=timeout, proxy=self.proxy_url, verify=self.ssl_context
        ) as client:
            # Start the request to get headers
            response = await client.send(
                client.build_request(
                    method=request_data["method"],
                    url=request_data["url"],
                    headers=request_data["headers"],
                    content=request_data["body"],
                ),
                stream=True,
            )

            # Check for errors before starting to stream
            if response.status_code >= 400:
                error_content = await response.aread()

                # Log the full error response body
                await self._log_verbose_api_response(
                    response.status_code, dict(response.headers), error_content, ctx
                )

                logger.info(
                    "streaming_error_received",
                    status_code=response.status_code,
                    error_detail=error_content.decode("utf-8", errors="replace"),
                )

                # Use transformer to handle error transformation (including OpenAI format)
                transformed_error_response = (
                    await response_transformer.transform_proxy_response(
                        response.status_code,
                        dict(response.headers),
                        error_content,
                        original_path,
                        proxy_mode,
                    )
                )
                transformed_error_body = transformed_error_response["body"]

                # Update context with error status
                ctx.add_metadata(status_code=response.status_code)

                # Log access log for error
                from ccproxy.observability.access_logger import log_request_access

                await log_request_access(
                    context=ctx,
                    status_code=response.status_code,
                    method=request_data["method"],
                    metrics=metrics,
                )

                # Return error as regular response
                return (
                    response.status_code,
                    dict(response.headers),
                    transformed_error_body,
                )

        # If no error, proceed with streaming
        response_headers = {}
        response_status = 200

        async with httpx.AsyncClient(
            timeout=timeout, proxy=self.proxy_url, verify=self.ssl_context
        ) as client:
            # Make initial request to capture headers
            initial_response = await client.send(
                client.build_request(
                    method=request_data["method"],
                    url=request_data["url"],
                    headers=request_data["headers"],
                    content=request_data["body"],
                ),
                stream=True,
            )
            response_status = initial_response.status_code
            response_headers = dict(initial_response.headers)

            # Close the initial response since we'll make a new one in the generator
            await initial_response.aclose()

        # Initialize streaming metrics collector
        metrics_collector = StreamingMetricsCollector(request_id=ctx.request_id)

        async def stream_generator() -> AsyncGenerator[bytes, None]:
            try:
                logger.debug(
                    "stream_generator_start",
                    method=request_data["method"],
                    url=request_data["url"],
                    headers=request_data["headers"],
                )

                start_time = time.perf_counter()
                async with (
                    httpx.AsyncClient(
                        timeout=timeout, proxy=self.proxy_url, verify=self.ssl_context
                    ) as client,
                    client.stream(
                        method=request_data["method"],
                        url=request_data["url"],
                        headers=request_data["headers"],
                        content=request_data["body"],
                    ) as response,
                ):
                    end_time = time.perf_counter()
                    proxy_api_call_ms = (end_time - start_time) * 1000
                    logger.debug(
                        "stream_response_received",
                        status_code=response.status_code,
                        headers=dict(response.headers),
                    )

                    # Log initial stream response headers if verbose
                    if self.verbose_api:
                        logger.info(
                            "verbose_api_stream_response_start",
                            status_code=response.status_code,
                            headers=self._redact_headers(dict(response.headers)),
                        )

                    # Store response status and headers
                    nonlocal response_status, response_headers
                    response_status = response.status_code
                    response_headers = dict(response.headers)

                    # Log upstream response headers for streaming
                    if self.verbose_api:
                        request_id = ctx.request_id
                        timestamp = ctx.get_log_timestamp_prefix()
                        await write_request_log(
                            request_id=request_id,
                            log_type="upstream_response_headers",
                            data={
                                "status_code": response.status_code,
                                "headers": dict(response.headers),
                                "stream_type": "anthropic_sse"
                                if not response_transformer._is_openai_request(
                                    original_path
                                )
                                else "openai_sse",
                            },
                            timestamp=timestamp,
                        )

                    # Transform streaming response
                    is_openai = response_transformer._is_openai_request(original_path)
                    logger.debug(
                        "openai_format_check", is_openai=is_openai, path=original_path
                    )

                    if is_openai:
                        # Transform Anthropic SSE to OpenAI SSE format using adapter
                        logger.debug("sse_transform_start", path=original_path)

                        # Get timestamp once for all streaming chunks
                        request_id = ctx.request_id
                        timestamp = ctx.get_log_timestamp_prefix()

                        async for (
                            transformed_chunk
                        ) in self._transform_anthropic_to_openai_stream(
                            response, original_path, openai_adapter
                        ):
                            # Log transformed streaming chunk
                            await append_streaming_log(
                                request_id=request_id,
                                log_type="upstream_streaming",
                                data=transformed_chunk,
                                timestamp=timestamp,
                            )

                            logger.debug(
                                "transformed_chunk_yielded",
                                chunk_size=len(transformed_chunk),
                            )
                            yield transformed_chunk
                    else:
                        # Stream as-is for Anthropic endpoints
                        logger.debug("anthropic_streaming_start")
                        chunk_count = 0
                        content_block_delta_count = 0

                        # Get timestamp once for all streaming chunks
                        request_id = ctx.request_id
                        timestamp = ctx.get_log_timestamp_prefix()

                        async for chunk in response.aiter_bytes():
                            if chunk:
                                chunk_count += 1

                                # Log raw streaming chunk
                                await append_streaming_log(
                                    request_id=request_id,
                                    log_type="upstream_streaming",
                                    data=chunk,
                                    timestamp=timestamp,
                                )

                                # Compact logging for content_block_delta events
                                chunk_str = chunk.decode("utf-8", errors="replace")

                                # Extract token metrics from streaming events
                                is_final = metrics_collector.process_chunk(chunk_str)

                                # If this is the final chunk with complete metrics, update context and record metrics
                                if is_final:
                                    model = ctx.metadata.get("model")
                                    cost_usd = metrics_collector.calculate_final_cost(
                                        model
                                    )
                                    final_metrics = metrics_collector.get_metrics()

                                    # Update context with final metrics
                                    ctx.add_metadata(
                                        status_code=response_status,
                                        tokens_input=final_metrics["tokens_input"],
                                        tokens_output=final_metrics["tokens_output"],
                                        cache_read_tokens=final_metrics[
                                            "cache_read_tokens"
                                        ],
                                        cache_write_tokens=final_metrics[
                                            "cache_write_tokens"
                                        ],
                                        cost_usd=cost_usd,
                                    )

                                if (
                                    "content_block_delta" in chunk_str
                                    and not self.verbose_streaming
                                ):
                                    content_block_delta_count += 1
                                    # Only log every 10th content_block_delta or when we start/end
                                    if content_block_delta_count == 1:
                                        logger.debug("content_block_delta_start")
                                    elif content_block_delta_count % 10 == 0:
                                        logger.debug(
                                            "content_block_delta_progress",
                                            count=content_block_delta_count,
                                        )
                                elif (
                                    self.verbose_streaming
                                    or "content_block_delta" not in chunk_str
                                ):
                                    # Log non-content_block_delta events normally, or everything if verbose mode
                                    logger.debug(
                                        "chunk_yielded",
                                        chunk_number=chunk_count,
                                        chunk_size=len(chunk),
                                        chunk_preview=chunk[:100].decode(
                                            "utf-8", errors="replace"
                                        ),
                                    )

                                yield chunk

                        # Final summary for content_block_delta events
                        if content_block_delta_count > 0 and not self.verbose_streaming:
                            logger.debug(
                                "content_block_delta_completed",
                                total_count=content_block_delta_count,
                            )

            except Exception as e:
                logger.exception("streaming_error", error=str(e), exc_info=True)
                error_message = f'data: {{"error": "Streaming error: {str(e)}"}}\\n\\n'
                yield error_message.encode("utf-8")

        # Always use upstream headers as base
        final_headers = response_headers.copy()

        # Remove headers that can cause conflicts
        final_headers.pop(
            "date", None
        )  # Remove upstream date header to avoid conflicts

        # Ensure critical headers for streaming
        final_headers["Cache-Control"] = "no-cache"
        final_headers["Connection"] = "keep-alive"

        # Set content-type if not already set by upstream
        if "content-type" not in final_headers:
            final_headers["content-type"] = "text/event-stream"

        return StreamingResponseWithLogging(
            content=stream_generator(),
            request_context=ctx,
            metrics=metrics,
            status_code=response_status,
            headers=final_headers,
        )

    async def _transform_anthropic_to_openai_stream(
        self, response: httpx.Response, original_path: str, openai_adapter: Any
    ) -> AsyncGenerator[bytes, None]:
        """Transform Anthropic SSE stream to OpenAI SSE format using adapter.

        Args:
            response: Streaming response from Anthropic
            original_path: Original request path for context
            openai_adapter: OpenAI adapter instance

        Yields:
            Transformed OpenAI SSE format chunks
        """

        # Parse SSE chunks from response into dict stream
        async def sse_to_dict_stream() -> AsyncGenerator[dict[str, object], None]:
            chunk_count = 0
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[6:].strip()
                    if data_str and data_str != "[DONE]":
                        try:
                            chunk_data = json.loads(data_str)
                            chunk_count += 1
                            logger.debug(
                                "proxy_anthropic_chunk_received",
                                chunk_count=chunk_count,
                                chunk_type=chunk_data.get("type"),
                                chunk=chunk_data,
                            )
                            yield chunk_data
                        except json.JSONDecodeError:
                            logger.warning("sse_parse_failed", data=data_str)
                            continue

        # Transform using OpenAI adapter and format back to SSE
        async for openai_chunk in openai_adapter.adapt_stream(sse_to_dict_stream()):
            sse_line = f"data: {json.dumps(openai_chunk)}\\n\\n"
            yield sse_line.encode("utf-8")

    async def _log_verbose_api_request(
        self, request_data: "RequestData", ctx: "RequestContext"
    ) -> None:
        """Log details of an outgoing API request if verbose logging is enabled."""
        if not self.verbose_api:
            return

        body = request_data.get("body")
        body_preview = ""
        full_body = None
        if body:
            try:
                full_body = body.decode("utf-8", errors="replace")
                # Truncate at 1024 chars for readability
                body_preview = full_body[:1024]
                # Try to parse as JSON for better formatting
                with contextlib.suppress(json.JSONDecodeError):
                    full_body = json.loads(full_body)
            except Exception:
                body_preview = f"<binary data of length {len(body)}>"

        logger.info(
            "verbose_api_request",
            method=request_data["method"],
            url=request_data["url"],
            headers=self._redact_headers(request_data["headers"]),
            body_size=len(body) if body else 0,
            body_preview=body_preview,
        )

        # Use new request logging system
        request_id = ctx.request_id
        timestamp = ctx.get_log_timestamp_prefix()
        await write_request_log(
            request_id=request_id,
            log_type="upstream_request",
            data={
                "method": request_data["method"],
                "url": request_data["url"],
                "headers": dict(request_data["headers"]),  # Don't redact in file
                "body": full_body,
            },
            timestamp=timestamp,
        )

    async def _log_verbose_api_response(
        self,
        status_code: int,
        headers: dict[str, str],
        body: bytes,
        ctx: "RequestContext",
    ) -> None:
        """Log details of a received API response if verbose logging is enabled."""
        if not self.verbose_api:
            return

        body_preview = ""
        if body:
            try:
                # Truncate at 1024 chars for readability
                body_preview = body.decode("utf-8", errors="replace")[:1024]
            except Exception:
                body_preview = f"<binary data of length {len(body)}>"

        logger.info(
            "verbose_api_response",
            status_code=status_code,
            headers=self._redact_headers(headers),
            body_size=len(body),
            body_preview=body_preview,
        )

        # Use new request logging system
        full_body = None
        if body:
            try:
                full_body_str = body.decode("utf-8", errors="replace")
                # Try to parse as JSON for better formatting
                try:
                    full_body = json.loads(full_body_str)
                except json.JSONDecodeError:
                    full_body = full_body_str
            except Exception:
                full_body = f"<binary data of length {len(body)}>"

        # Use new request logging system
        request_id = ctx.request_id
        timestamp = ctx.get_log_timestamp_prefix()
        await write_request_log(
            request_id=request_id,
            log_type="upstream_response",
            data={
                "status_code": status_code,
                "headers": dict(headers),  # Don't redact in file
                "body": full_body,
            },
            timestamp=timestamp,
        )

    def _redact_headers(self, headers: dict[str, str]) -> dict[str, str]:
        """Redact sensitive information from headers for safe logging."""
        SENSITIVE_HEADERS = {"authorization", "x-api-key", "cookie", "set-cookie"}
        return {
            k: "[REDACTED]" if k.lower() in SENSITIVE_HEADERS else v
            for k, v in headers.items()
        }
