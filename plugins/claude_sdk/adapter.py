"""Claude SDK adapter implementation using delegation pattern."""

import json
from collections.abc import AsyncIterator
from typing import Any

import structlog
from fastapi import HTTPException, Request
from starlette.responses import Response, StreamingResponse

from ccproxy.services.adapters.base import BaseAdapter

from .auth import NoOpAuthManager
from .format_adapter import ClaudeSDKFormatAdapter
from .handler import ClaudeSDKHandler
from .transformers.request import ClaudeSDKRequestTransformer
from .transformers.response import ClaudeSDKResponseTransformer


logger = structlog.get_logger(__name__)


class ClaudeSDKAdapter(BaseAdapter):
    """Claude SDK adapter implementation using delegation pattern.

    This adapter delegates to ProxyService for request handling,
    following the same pattern as claude_api and codex plugins.
    """

    def __init__(self) -> None:
        """Initialize the Claude SDK adapter."""
        self.logger = structlog.get_logger(__name__)
        self.proxy_service: Any | None = None
        self.handler: ClaudeSDKHandler | None = None
        self.format_adapter: ClaudeSDKFormatAdapter | None = None
        self.request_transformer: ClaudeSDKRequestTransformer | None = None
        self.response_transformer: ClaudeSDKResponseTransformer | None = None
        self.auth_manager: NoOpAuthManager | None = None
        self._initialized = False
        self._detection_service: Any | None = None

    def set_detection_service(self, detection_service: Any) -> None:
        """Set the detection service.

        Args:
            detection_service: Claude CLI detection service
        """
        self._detection_service = detection_service

    def set_proxy_service(self, proxy_service: Any) -> None:
        """Set the proxy service for request handling.

        Args:
            proxy_service: ProxyService instance for handling requests
        """
        self.proxy_service = proxy_service

    def _ensure_initialized(self, request: Request) -> None:
        """Ensure adapter is properly initialized.

        Args:
            request: FastAPI request object

        Raises:
            HTTPException: If initialization fails
        """
        if self._initialized:
            return

        try:
            # Get proxy service from app state if not set
            if not self.proxy_service:
                proxy_service = getattr(request.app.state, "proxy_service", None)
                if not proxy_service:
                    raise HTTPException(
                        status_code=503, detail="Proxy service not available"
                    )
                self.proxy_service = proxy_service

            # Initialize components
            from ccproxy.config.settings import get_settings

            settings = get_settings()

            # Create handler with SDK service logic
            self.handler = ClaudeSDKHandler(settings=settings)

            # Create format adapter for OpenAI conversion
            self.format_adapter = ClaudeSDKFormatAdapter()

            # Create transformers
            self.request_transformer = ClaudeSDKRequestTransformer()
            self.response_transformer = ClaudeSDKResponseTransformer()

            # Create auth manager (no-op for SDK)
            self.auth_manager = NoOpAuthManager()

            self._initialized = True
            self.logger.debug("Claude SDK adapter initialized successfully")

        except Exception as e:
            self.logger.error(f"Failed to initialize Claude SDK adapter: {e}")
            raise HTTPException(
                status_code=503, detail=f"Claude SDK initialization failed: {str(e)}"
            ) from e

    async def handle_request(
        self, request: Request, endpoint: str, method: str, **kwargs: Any
    ) -> Response | StreamingResponse:
        """Handle a request through Claude SDK.

        This method is called by ProxyService when it encounters the special
        claude-sdk:// protocol. We handle the request directly using the handler
        rather than delegating back to avoid circular routing.

        Args:
            request: FastAPI request object
            endpoint: Target endpoint path
            method: HTTP method
            **kwargs: Additional arguments

        Returns:
            Response from Claude SDK
        """
        self._ensure_initialized(request)

        # Parse request body
        body = await request.body()
        if not body:
            raise HTTPException(status_code=400, detail="Request body is required")

        try:
            request_data = json.loads(body)
        except json.JSONDecodeError as e:
            raise HTTPException(
                status_code=400, detail=f"Invalid JSON: {str(e)}"
            ) from e

        # Check if format conversion is needed (OpenAI to Anthropic)
        needs_conversion = endpoint == "/v1/chat/completions"
        if needs_conversion and self.format_adapter:
            request_data = await self.format_adapter.adapt_request(request_data)

        # Extract parameters for SDK handler
        messages = request_data.get("messages", [])
        model = request_data.get("model", "claude-3-opus-20240229")
        temperature = request_data.get("temperature")
        max_tokens = request_data.get("max_tokens")
        stream = request_data.get("stream", False)
        session_id = request_data.get("session_id")

        # Get or create request context for observability
        request_context = getattr(request.state, "context", None)
        if not request_context:
            # Create a new context if one doesn't exist
            import time
            import uuid

            import structlog

            from ccproxy.observability.context import RequestContext

            request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
            request_logger = structlog.get_logger().bind(
                request_id=request_id,
                provider="claude_sdk",
                endpoint=endpoint,
            )

            request_context = RequestContext(
                request_id=request_id,
                start_time=time.perf_counter(),
                logger=request_logger,
                metadata={
                    "provider": "claude_sdk",
                    "endpoint": endpoint,
                    "method": method,
                },
            )

        self.logger.info(
            "claude_sdk_direct_handling",
            endpoint=endpoint,
            model=model,
            stream=stream,
            needs_conversion=needs_conversion,
        )

        try:
            # Call handler directly to create completion
            if not self.handler:
                raise HTTPException(status_code=503, detail="Handler not initialized")

            result = await self.handler.create_completion(
                request_context=request_context,
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=stream,
                session_id=session_id,
                **{
                    k: v
                    for k, v in request_data.items()
                    if k
                    not in [
                        "messages",
                        "model",
                        "temperature",
                        "max_tokens",
                        "stream",
                        "session_id",
                    ]
                },
            )

            if stream:
                # Return streaming response
                async def stream_generator() -> AsyncIterator[bytes]:
                    """Generate SSE stream from handler's async iterator."""
                    try:
                        if needs_conversion:
                            # Use OpenAIStreamProcessor to convert Claude SSE to OpenAI SSE format
                            from ccproxy.adapters.openai.streaming import (
                                OpenAIStreamProcessor,
                            )

                            # Create processor with SSE output format
                            processor = OpenAIStreamProcessor(
                                model=model,
                                enable_usage=True,
                                enable_tool_calls=True,
                                output_format="sse",  # Generate SSE strings
                            )

                            # Process the stream and yield SSE formatted chunks
                            async for sse_chunk in processor.process_stream(result):  # type: ignore[union-attr]
                                # sse_chunk is already a formatted SSE string when output_format="sse"
                                if isinstance(sse_chunk, str):
                                    yield sse_chunk.encode()
                                else:
                                    # Should not happen, but handle gracefully
                                    yield str(sse_chunk).encode()
                        else:
                            # Pass through Claude SSE format as-is
                            async for chunk in result:  # type: ignore[union-attr]
                                if isinstance(chunk, dict):
                                    import json

                                    data = json.dumps(chunk)
                                    yield f"data: {data}\n\n".encode()
                                else:
                                    yield (
                                        chunk
                                        if isinstance(chunk, bytes)
                                        else str(chunk).encode()
                                    )
                    except Exception as e:
                        self.logger.error(f"Streaming error: {e}")
                        error_chunk = {"error": str(e)}
                        yield f"data: {json.dumps(error_chunk)}\n\n".encode()
                        # Don't add extra [DONE] here as OpenAIStreamProcessor already adds it

                return StreamingResponse(
                    stream_generator(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Claude-SDK-Response": "true",
                    },
                )
            else:
                # Convert MessageResponse to dict for JSON response
                from ccproxy.models.messages import MessageResponse

                if isinstance(result, MessageResponse):
                    response_data = result.model_dump()
                else:
                    response_data = result

                # Convert to OpenAI format if needed
                if needs_conversion and self.format_adapter:
                    response_data = await self.format_adapter.adapt_response(
                        response_data
                    )

                return Response(
                    content=json.dumps(response_data),
                    media_type="application/json",
                    headers={
                        "X-Claude-SDK-Response": "true",
                    },
                )

        except Exception as e:
            self.logger.error(f"Failed to handle SDK request: {e}")
            raise HTTPException(
                status_code=500, detail=f"SDK request failed: {str(e)}"
            ) from e

    async def handle_streaming(
        self, request: Request, endpoint: str, **kwargs: Any
    ) -> StreamingResponse:
        """Handle a streaming request through Claude SDK.

        This is a convenience method that ensures stream=true and delegates
        to handle_request which handles both streaming and non-streaming.

        Args:
            request: FastAPI request object
            endpoint: Target endpoint path
            **kwargs: Additional arguments

        Returns:
            Streaming response from Claude SDK
        """
        self._ensure_initialized(request)

        # Parse and modify request to ensure stream=true
        body = await request.body()
        if not body:
            request_data = {"stream": True}
        else:
            try:
                request_data = json.loads(body)
            except json.JSONDecodeError:
                request_data = {"stream": True}

        # Force streaming
        request_data["stream"] = True
        modified_body = json.dumps(request_data).encode()

        # Create modified request with stream=true
        modified_scope = {
            **request.scope,
            "_body": modified_body,
        }

        from starlette.requests import Request as StarletteRequest

        modified_request = StarletteRequest(
            scope=modified_scope,
            receive=request.receive,
        )
        modified_request._body = modified_body

        # Delegate to handle_request which will handle streaming
        result = await self.handle_request(modified_request, endpoint, "POST", **kwargs)

        # Ensure we return a streaming response
        if not isinstance(result, StreamingResponse):
            # This shouldn't happen since we forced stream=true, but handle it gracefully
            self.logger.warning("Expected StreamingResponse but got regular Response")
            return StreamingResponse(
                iter([result.body if hasattr(result, "body") else b""]),
                media_type="text/event-stream",
                headers={"X-Claude-SDK-Response": "true"},
            )

        return result

    async def close(self) -> None:
        """Cleanup resources when shutting down."""
        if self.handler:
            await self.handler.close()
        self._initialized = False
        self.logger.debug("Claude SDK adapter cleaned up")
