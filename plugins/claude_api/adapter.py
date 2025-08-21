"""Claude API adapter implementation."""

import json
from typing import Any

import structlog
from fastapi import HTTPException, Request
from httpx import AsyncClient
from starlette.responses import Response, StreamingResponse

from ccproxy.config.constants import (
    CLAUDE_API_BASE_URL,
    CLAUDE_MESSAGES_ENDPOINT,
    OPENAI_CHAT_COMPLETIONS_PATH,
)
from ccproxy.services.adapters.base import BaseAdapter
from ccproxy.services.handler_config import HandlerConfig
from ccproxy.services.http.plugin_handler import PluginHTTPHandler

from .transformers import ClaudeAPIRequestTransformer, ClaudeAPIResponseTransformer


logger = structlog.get_logger(__name__)


class ClaudeAPIAdapter(BaseAdapter):
    """Claude API adapter implementation.

    This adapter provides direct access to the Anthropic Claude API
    with support for both native Anthropic format and OpenAI-compatible format.
    """

    def __init__(
        self,
        proxy_service: Any | None,
        auth_manager: Any,
        detection_service: Any,
        http_client: AsyncClient | None = None,
        logger: structlog.BoundLogger | None = None,
    ) -> None:
        """Initialize the Claude API adapter.

        Args:
            proxy_service: ProxyService instance for handling requests
            auth_manager: Authentication manager for credentials
            detection_service: Detection service for Claude CLI detection
            http_client: Optional HTTP client for making requests
            logger: Optional structured logger instance
        """
        self.logger = logger or structlog.get_logger(__name__)
        self.proxy_service = proxy_service
        self._auth_manager = auth_manager
        self._detection_service = detection_service

        # Initialize OpenAI adapter for format conversion
        from ccproxy.adapters.openai.adapter import OpenAIAdapter

        self.openai_adapter: OpenAIAdapter | None = OpenAIAdapter()

        # Initialize HTTP handler
        request_tracer = None
        if proxy_service and hasattr(proxy_service, "request_tracer"):
            request_tracer = proxy_service.request_tracer

        if http_client:
            self._http_handler: PluginHTTPHandler = PluginHTTPHandler(
                http_client=http_client, request_tracer=request_tracer
            )
        elif proxy_service and hasattr(proxy_service, "http_client"):
            self._http_handler = PluginHTTPHandler(
                http_client=proxy_service.http_client, request_tracer=request_tracer
            )
        else:
            raise RuntimeError(
                "No HTTP client available - provide http_client or proxy_service with http_client"
            )

        # Initialize transformers
        self._request_transformer: ClaudeAPIRequestTransformer | None = (
            ClaudeAPIRequestTransformer(detection_service)
        )

        # Get CORS settings if available
        cors_settings = None
        if proxy_service and hasattr(proxy_service, "config"):
            cors_settings = getattr(proxy_service.config, "cors", None)
        self._response_transformer: ClaudeAPIResponseTransformer | None = (
            ClaudeAPIResponseTransformer(cors_settings)
        )

    async def handle_request(
        self, request: Request, endpoint: str, method: str, **kwargs: Any
    ) -> Response | StreamingResponse:
        """Handle a request to the Claude API.

        Args:
            request: FastAPI request object
            endpoint: Target endpoint path
            method: HTTP method
            **kwargs: Additional arguments

        Returns:
            Response from Claude API
        """
        # Validate prerequisites
        self._validate_prerequisites()

        # Get request body and auth
        body = await request.body()
        auth_headers = await self._auth_manager.get_auth_headers()
        access_token = auth_headers.get("x-api-key") if auth_headers else None

        # Determine endpoint handling
        target_url, needs_conversion = self._resolve_endpoint(endpoint)

        # Create handler configuration
        handler_config = self._create_handler_config(needs_conversion)

        # Prepare and execute request
        return await self._execute_request(
            method=method,
            target_url=target_url,
            body=body,
            auth_headers=auth_headers,
            access_token=access_token,
            request_headers=dict(request.headers),
            handler_config=handler_config,
            endpoint=endpoint,
            needs_conversion=needs_conversion,
        )

    def _validate_prerequisites(self) -> None:
        """Validate that required components are available."""
        if not self._auth_manager:
            raise HTTPException(
                status_code=503, detail="Authentication manager not available"
            )
        if not self._http_handler:
            raise HTTPException(status_code=503, detail="HTTP handler not initialized")

    def _resolve_endpoint(self, endpoint: str) -> tuple[str, bool]:
        """Resolve the target URL and determine if format conversion is needed.

        Args:
            endpoint: The requested endpoint path

        Returns:
            Tuple of (target_url, needs_conversion)
        """
        if endpoint.endswith(CLAUDE_MESSAGES_ENDPOINT):
            # Native Anthropic format
            return f"{CLAUDE_API_BASE_URL}{CLAUDE_MESSAGES_ENDPOINT}", False
        elif endpoint.endswith(OPENAI_CHAT_COMPLETIONS_PATH):
            # OpenAI format - needs conversion
            return f"{CLAUDE_API_BASE_URL}{CLAUDE_MESSAGES_ENDPOINT}", True
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Endpoint {endpoint} not supported by Claude API plugin",
            )

    def _create_handler_config(self, needs_conversion: bool) -> HandlerConfig:
        """Create handler configuration based on conversion needs.

        Args:
            needs_conversion: Whether format conversion is needed

        Returns:
            HandlerConfig instance
        """
        return HandlerConfig(
            request_adapter=self.openai_adapter if needs_conversion else None,
            response_adapter=self.openai_adapter if needs_conversion else None,
            request_transformer=self._request_transformer,
            response_transformer=self._response_transformer,
            supports_streaming=True,
        )

    async def _execute_request(
        self,
        method: str,
        target_url: str,
        body: bytes,
        auth_headers: dict[str, str],
        access_token: str | None,
        request_headers: dict[str, str],
        handler_config: HandlerConfig,
        endpoint: str,
        needs_conversion: bool,
    ) -> Response | StreamingResponse:
        """Execute the HTTP request.

        Args:
            method: HTTP method
            target_url: Target API URL
            body: Request body
            auth_headers: Authentication headers
            access_token: Access token if available
            request_headers: Original request headers
            handler_config: Handler configuration
            endpoint: Original endpoint for logging
            needs_conversion: Whether conversion was needed for logging

        Returns:
            Response or StreamingResponse
        """
        # Handler is guaranteed to exist after _validate_prerequisites
        assert self._http_handler is not None

        # Prepare request
        (
            transformed_body,
            headers,
            is_streaming,
        ) = await self._http_handler.prepare_request(
            request_body=body,
            handler_config=handler_config,
            auth_headers=auth_headers,
            request_headers=request_headers,
            access_token=access_token,
        )

        # Parse request body to extract model and other metadata
        try:
            request_data = json.loads(transformed_body) if transformed_body else {}
        except json.JSONDecodeError:
            request_data = {}

        # Get or create RequestContext
        from ccproxy.observability.context import RequestContext

        request_context = RequestContext.get_current()
        if request_context:
            # Update existing context with claude_api specific metadata
            request_context.metadata.update(
                {
                    "provider": "claude_api",
                    "service_type": "claude_api",
                    "endpoint": endpoint.rstrip("/").split("/")[-1]
                    if endpoint
                    else "messages",
                    "model": request_data.get("model", "unknown"),
                    "stream": is_streaming,
                    "needs_conversion": needs_conversion,
                }
            )
        else:
            # Create new context if none exists (shouldn't happen with ProxyService)
            import time
            import uuid

            request_context = RequestContext(
                request_id=str(uuid.uuid4()),
                start_time=time.time(),
                logger=self.logger,
                metadata={
                    "provider": "claude_api",
                    "service_type": "claude_api",
                    "endpoint": endpoint.rstrip("/").split("/")[-1]
                    if endpoint
                    else "messages",
                    "model": request_data.get("model", "unknown"),
                    "stream": is_streaming,
                    "needs_conversion": needs_conversion,
                },
            )

        self.logger.info(
            "claude_api_request",
            endpoint=endpoint,
            target_url=target_url,
            needs_conversion=needs_conversion,
            is_streaming=is_streaming,
            model=request_context.metadata.get("model"),
        )

        # Get streaming handler if needed
        streaming_handler = None
        if is_streaming and self.proxy_service:
            streaming_handler = getattr(self.proxy_service, "streaming_handler", None)

        # Execute request with proper request_context
        response = await self._http_handler.handle_request(
            method=method,
            url=target_url,
            headers=headers,
            body=transformed_body,
            handler_config=handler_config,
            is_streaming=is_streaming,
            streaming_handler=streaming_handler,
            request_context=request_context,  # Pass the actual RequestContext object
        )

        # For streaming responses, wrap to accumulate chunks and extract headers
        if is_streaming and isinstance(response, StreamingResponse):
            return await self._wrap_streaming_response(response, request_context)

        return response

    async def _wrap_streaming_response(
        self, response: StreamingResponse, request_context: Any
    ) -> StreamingResponse:
        """Wrap streaming response to accumulate chunks and extract headers.

        Args:
            response: The streaming response to wrap
            request_context: The request context to update

        Returns:
            Wrapped streaming response
        """
        from collections.abc import AsyncIterator

        # Get the original iterator
        original_iterator = response.body_iterator

        # Create accumulator for chunks
        chunks: list[bytes] = []
        headers_extracted = False

        # Create metrics collector for usage extraction
        from ccproxy.utils.streaming_metrics import StreamingMetricsCollector

        collector = StreamingMetricsCollector(request_id=request_context.request_id)

        async def wrapped_iterator() -> AsyncIterator[bytes]:
            """Wrap the stream iterator to accumulate chunks."""
            nonlocal headers_extracted

            async for chunk in original_iterator:
                # Extract headers on first chunk (after streaming has started)
                if not headers_extracted:
                    headers_extracted = True
                    if "response_headers" in request_context.metadata:
                        response_headers = request_context.metadata["response_headers"]

                        # Extract relevant headers and put them directly in metadata for access_logger
                        headers_for_log = {}
                        for k, v in response_headers.items():
                            k_lower = k.lower()
                            # Include Anthropic headers and request IDs
                            if k_lower.startswith("anthropic-ratelimit"):
                                # Put rate limit headers directly in metadata for access_logger
                                request_context.metadata[k_lower] = v
                                headers_for_log[k] = v
                            elif k_lower == "anthropic-request-id":
                                # Also store request ID
                                request_context.metadata["anthropic_request_id"] = v
                                headers_for_log[k] = v
                            elif "request" in k_lower and "id" in k_lower:
                                headers_for_log[k] = v

                        # Also store the headers dictionary for display
                        request_context.metadata["headers"] = headers_for_log

                        self.logger.debug(
                            "claude_api_headers_extracted",
                            headers_count=len(headers_for_log),
                            headers=headers_for_log,
                            direct_metadata_keys=[
                                k
                                for k in request_context.metadata
                                if "anthropic" in k.lower()
                            ],
                        )

                if isinstance(chunk, str | memoryview):
                    chunk = chunk.encode() if isinstance(chunk, str) else bytes(chunk)
                chunks.append(chunk)

                # Process this chunk for usage data
                chunk_str = chunk.decode("utf-8", errors="ignore")
                is_final = collector.process_chunk(chunk_str)

                # If we got final metrics, update context
                if is_final:
                    usage_metrics = collector.get_metrics()
                    if usage_metrics:
                        # Calculate cost if we have model info
                        model = request_context.metadata.get("model")
                        if model:
                            cost_usd = collector.calculate_final_cost(model)
                        else:
                            cost_usd = usage_metrics.get("cost_usd")

                        # Update request context with usage data
                        request_context.metadata.update(
                            {
                                "tokens_input": usage_metrics.get("tokens_input", 0),
                                "tokens_output": usage_metrics.get("tokens_output", 0),
                                "tokens_total": (
                                    (usage_metrics.get("tokens_input") or 0)
                                    + (usage_metrics.get("tokens_output") or 0)
                                ),
                                "cost_usd": cost_usd or 0.0,
                                "cache_read_tokens": usage_metrics.get(
                                    "cache_read_tokens"
                                ),
                                "cache_write_tokens": usage_metrics.get(
                                    "cache_write_tokens"
                                ),
                            }
                        )

                yield chunk

            # Mark that stream processing is complete
            request_context.metadata.update(
                {
                    "stream_accumulated": True,
                    "stream_chunks_count": len(chunks),
                }
            )

        # Create new streaming response with wrapped iterator
        return StreamingResponse(
            wrapped_iterator(),
            status_code=response.status_code,
            headers=dict(response.headers) if hasattr(response, "headers") else {},
            media_type=response.media_type,
        )

    async def handle_streaming(
        self, request: Request, endpoint: str, **kwargs: Any
    ) -> StreamingResponse:
        """Handle a streaming request to the Claude API.

        Forces stream=true in the request body and delegates to handle_request.

        Args:
            request: FastAPI request object
            endpoint: Target endpoint path
            **kwargs: Additional arguments

        Returns:
            Streaming response from Claude API
        """
        # Modify request to force streaming
        modified_request = await self._create_streaming_request(request)

        # Delegate to handle_request
        result = await self.handle_request(modified_request, endpoint, "POST", **kwargs)

        # Ensure streaming response
        if isinstance(result, StreamingResponse):
            return result

        # Fallback: wrap non-streaming response
        return StreamingResponse(
            iter([result.body if hasattr(result, "body") else b""]),
            media_type="text/event-stream",
        )

    async def _create_streaming_request(self, request: Request) -> Request:
        """Create a modified request with stream=true.

        Args:
            request: Original request

        Returns:
            Modified request with stream=true
        """
        body = await request.body()

        # Parse and modify request data
        try:
            request_data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            request_data = {}

        request_data["stream"] = True
        modified_body = json.dumps(request_data).encode()

        # Create modified request
        from starlette.requests import Request as StarletteRequest

        modified_scope = {**request.scope, "_body": modified_body}
        modified_request = StarletteRequest(
            scope=modified_scope,
            receive=request.receive,
        )
        modified_request._body = modified_body

        return modified_request

    async def cleanup(self) -> None:
        """Cleanup resources when shutting down."""
        try:
            # Cleanup HTTP handler
            if self._http_handler and hasattr(self._http_handler, "cleanup"):
                await self._http_handler.cleanup()

            # Note: We don't clear _http_handler as it's not Optional anymore
            self.proxy_service = None
            self._request_transformer = None
            self._response_transformer = None
            self.openai_adapter = None

            self.logger.debug("claude_api_adapter_cleanup_completed")

        except Exception as e:
            self.logger.error(
                "claude_api_adapter_cleanup_failed",
                error=str(e),
                exc_info=e,
            )
