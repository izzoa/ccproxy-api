"""Claude SDK adapter implementation."""

import json
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import structlog
from fastapi import HTTPException, Request
from httpx import AsyncClient
from starlette.responses import Response, StreamingResponse

from ccproxy.services.adapters.base import BaseAdapter

from .detection_service import ClaudeSDKDetectionService


logger = structlog.get_logger(__name__)


class ClaudeSDKAdapter(BaseAdapter):
    """Claude SDK adapter implementation.

    This adapter provides access to Claude through the Claude Code SDK,
    enabling MCP tools and other SDK-specific features.
    """

    def __init__(
        self,
        http_client: AsyncClient,
        logger: structlog.BoundLogger,
    ) -> None:
        """Initialize the Claude SDK adapter.

        Args:
            http_client: Shared HTTP client (unused for SDK)
            logger: Bound logger for this adapter
        """
        super().__init__()
        self.http_client = http_client  # Not used but required by protocol
        self.logger = logger
        self.claude_service: Any = None
        self.adapter: Any = None
        self._detection_service: ClaudeSDKDetectionService | None = None
        self._initialized = False

    def set_detection_service(
        self, detection_service: ClaudeSDKDetectionService
    ) -> None:
        """Set the detection service for this adapter.

        Args:
            detection_service: Claude CLI detection service
        """
        self._detection_service = detection_service

    def _lazy_init(self) -> None:
        """Lazy initialization to avoid import issues during plugin discovery."""
        if self._initialized:
            return

        try:
            # Import dependencies only when needed
            from ccproxy.adapters.openai.adapter import OpenAIAdapter
            from ccproxy.config.settings import get_settings
            from ccproxy.observability import get_metrics
            from ccproxy.services.claude_sdk_service import ClaudeSDKService

            settings = get_settings()
            metrics = get_metrics()

            # Create ClaudeSDKService instance
            self.claude_service = ClaudeSDKService(
                metrics=metrics,
                settings=settings,
                session_manager=None,  # Could be enhanced to support pooling
            )

            # Create OpenAI adapter for format conversion
            self.adapter = OpenAIAdapter()

            self._initialized = True
            self.logger.info("claude_sdk_adapter_initialized")

        except Exception as e:
            self.logger.error(f"Failed to initialize Claude SDK adapter: {e}")
            raise HTTPException(
                status_code=503, detail=f"Claude SDK initialization failed: {str(e)}"
            ) from e

    async def handle_request(
        self, request: Request, endpoint: str, method: str, **kwargs: Any
    ) -> Response:
        """Handle a request through Claude SDK.

        Args:
            request: FastAPI request object
            endpoint: Target endpoint path
            method: HTTP method
            **kwargs: Additional arguments

        Returns:
            Response from Claude SDK
        """
        self._lazy_init()

        # Get request body
        body = await request.body()

        try:
            request_data = json.loads(body) if body else {}
        except json.JSONDecodeError as e:
            raise HTTPException(
                status_code=400, detail="Invalid JSON in request body"
            ) from e

        # Get request context
        request_context = getattr(request.state, "context", None)
        if request_context is None:
            # Create a minimal context if not available
            from ccproxy.observability.context import RequestContext

            request_context = RequestContext(
                request_id=str(uuid.uuid4()),
                start_time=time.perf_counter(),
                logger=self.logger,
            )

        # Route to appropriate SDK method based on endpoint
        if endpoint == "/v1/messages" and method == "POST":
            # Handle native Anthropic messages endpoint
            response = await self.claude_service.create_completion(
                messages=request_data.get("messages", []),
                model=request_data.get("model", "claude-3-5-sonnet-20241022"),
                temperature=request_data.get("temperature"),
                max_tokens=request_data.get("max_tokens"),
                stream=False,
                user_id=request_data.get("user"),
                request_context=request_context,
            )

            # Convert response to dict
            response_dict = (
                response.model_dump() if hasattr(response, "model_dump") else response
            )

            return Response(
                content=json.dumps(response_dict),
                status_code=200,
                media_type="application/json",
            )

        elif endpoint == "/v1/chat/completions" and method == "POST":
            # Handle OpenAI-compatible endpoint
            # Convert OpenAI request to Anthropic format
            anthropic_request = await self.adapter.adapt_request(request_data)

            # Call Claude SDK with adapted request
            response = await self.claude_service.create_completion(
                messages=anthropic_request["messages"],
                model=anthropic_request["model"],
                temperature=anthropic_request.get("temperature"),
                max_tokens=anthropic_request.get("max_tokens"),
                stream=False,
                user_id=request_data.get("user"),
                request_context=request_context,
            )

            # Convert response to dict and then to OpenAI format
            response_dict = (
                response.model_dump() if hasattr(response, "model_dump") else response
            )
            openai_response = await self.adapter.adapt_response(response_dict)

            return Response(
                content=json.dumps(openai_response),
                status_code=200,
                media_type="application/json",
            )

        else:
            raise HTTPException(
                status_code=404,
                detail=f"Endpoint {endpoint} not supported by Claude SDK plugin",
            )

    async def handle_streaming(
        self, request: Request, endpoint: str, **kwargs: Any
    ) -> StreamingResponse:
        """Handle a streaming request through Claude SDK.

        Args:
            request: FastAPI request object
            endpoint: Target endpoint path
            **kwargs: Additional arguments

        Returns:
            Streaming response from Claude SDK
        """
        self._lazy_init()

        # Get request body
        body = await request.body()

        try:
            request_data = json.loads(body) if body else {}
        except json.JSONDecodeError as e:
            raise HTTPException(
                status_code=400, detail="Invalid JSON in request body"
            ) from e

        # Check if streaming is requested
        if not request_data.get("stream", False):
            # Non-streaming request, use regular handler
            response = await self.handle_request(request, endpoint, "POST", **kwargs)
            return StreamingResponse(
                iter([response.body]),
                media_type="application/json",
            )

        # Get request context
        request_context = getattr(request.state, "context", None)
        if request_context is None:
            from ccproxy.observability.context import RequestContext

            request_context = RequestContext(
                request_id=str(uuid.uuid4()),
                start_time=time.perf_counter(),
                logger=self.logger,
            )

        async def stream_generator() -> AsyncGenerator[bytes, None]:
            """Generate streaming response from Claude SDK."""
            if endpoint == "/v1/messages":
                # Stream native Anthropic format
                response = await self.claude_service.create_completion(
                    messages=request_data.get("messages", []),
                    model=request_data.get("model", "claude-3-5-sonnet-20241022"),
                    temperature=request_data.get("temperature"),
                    max_tokens=request_data.get("max_tokens"),
                    stream=True,
                    user_id=request_data.get("user"),
                    request_context=request_context,
                )

                # Stream the response
                async for chunk in response:
                    if isinstance(chunk, dict):
                        yield f"data: {json.dumps(chunk)}\n\n".encode()
                    elif isinstance(chunk, str):
                        yield f"data: {chunk}\n\n".encode()
                    else:
                        # Handle model objects
                        chunk_dict = (
                            chunk.model_dump()
                            if hasattr(chunk, "model_dump")
                            else str(chunk)
                        )
                        yield f"data: {json.dumps(chunk_dict)}\n\n".encode()

                yield b"data: [DONE]\n\n"

            elif endpoint == "/v1/chat/completions":
                # Stream OpenAI format
                # Convert OpenAI request to Anthropic format
                anthropic_request = await self.adapter.adapt_request(request_data)

                # Get streaming response from Claude SDK
                response = await self.claude_service.create_completion(
                    messages=anthropic_request["messages"],
                    model=anthropic_request["model"],
                    temperature=anthropic_request.get("temperature"),
                    max_tokens=anthropic_request.get("max_tokens"),
                    stream=True,
                    user_id=request_data.get("user"),
                    request_context=request_context,
                )

                # Convert and stream OpenAI format chunks
                async for openai_chunk in self.adapter.adapt_stream(response):
                    yield f"data: {json.dumps(openai_chunk)}\n\n".encode()

                yield b"data: [DONE]\n\n"

            else:
                raise HTTPException(
                    status_code=404,
                    detail=f"Streaming not supported for endpoint {endpoint}",
                )

        return StreamingResponse(
            stream_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    async def cleanup(self) -> None:
        """Clean up adapter resources."""
        if self.claude_service:
            await self.claude_service.close()
        self.logger.info("claude_sdk_adapter_cleanup_completed")
