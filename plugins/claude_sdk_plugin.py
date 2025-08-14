"""Claude SDK provider plugin."""

import json
from collections.abc import AsyncGenerator
from typing import Any

import structlog
from fastapi import HTTPException, Request
from starlette.responses import Response, StreamingResponse

from ccproxy.models.provider import ProviderConfig
from ccproxy.services.adapters.base import BaseAdapter


logger = structlog.get_logger(__name__)


class ClaudeSDKAdapter(BaseAdapter):
    """Claude SDK adapter implementation.

    This plugin provides access to Claude through the Claude Code SDK,
    enabling MCP tools and other SDK-specific features.
    """

    def __init__(self) -> None:
        """Initialize the Claude SDK adapter."""
        self.claude_service = None
        self.adapter = None
        self._initialized = False

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
        except Exception as e:
            logger.error(f"Failed to initialize Claude SDK adapter: {e}")
            raise HTTPException(
                status_code=503, detail=f"Claude SDK initialization failed: {str(e)}"
            )

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
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON in request body")

        # Get request context
        request_context = getattr(request.state, "context", None)
        if request_context is None:
            # Create a minimal context if not available
            from ccproxy.observability.context import RequestContext

            request_context = RequestContext()

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
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON in request body")

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

            request_context = RequestContext()

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


class ClaudeSDKPlugin:
    """Claude SDK provider plugin.

    This plugin provides integration with Claude through the Claude Code SDK,
    enabling advanced features like MCP tools, system messages, and session management.
    """

    @property
    def name(self) -> str:
        """Plugin name."""
        return "claude-sdk"

    @property
    def version(self) -> str:
        """Plugin version."""
        return "1.0.0"

    def create_adapter(self) -> BaseAdapter:
        """Create adapter instance."""
        return ClaudeSDKAdapter()

    def create_config(self) -> ProviderConfig:
        """Create provider configuration."""
        return ProviderConfig(
            name="claude-sdk",
            base_url="claude-sdk://local",  # Special URL for SDK
            supports_streaming=True,
            requires_auth=False,  # SDK handles auth internally
            auth_type=None,
            models=[
                "claude-3-5-sonnet-20241022",
                "claude-3-5-haiku-20241022",
                "claude-3-opus-20240229",
                "claude-3-sonnet-20240229",
                "claude-3-haiku-20240307",
            ],
        )

    async def validate(self) -> bool:
        """Validate plugin is ready.

        Checks that Claude CLI is available.
        """
        # Simple check for Claude CLI
        import shutil
        from pathlib import Path

        # Check common locations for Claude CLI
        claude_paths = [
            Path.home() / ".cache" / ".bun" / "bin" / "claude",
            Path.home() / ".local" / "bin" / "claude",
            shutil.which("claude"),
        ]

        for path in claude_paths:
            if path and Path(str(path)).exists():
                logger.info(
                    f"Claude SDK plugin validation successful: CLI found at {path}"
                )
                return True

        logger.warning("Claude SDK plugin validation failed: Claude CLI not found")
        return False
