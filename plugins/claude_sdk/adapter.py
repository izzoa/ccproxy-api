"""Claude SDK adapter implementation - handles requests directly."""

import contextlib
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import structlog
from fastapi import HTTPException, Request
from starlette.responses import Response, StreamingResponse

from ccproxy.services.adapters.base import BaseAdapter

from .auth import NoOpAuthManager
from .client import ClaudeSDKClient
from .converter import MessageConverter
from .options import OptionsHandler


logger = structlog.get_logger(__name__)


class ClaudeSDKAdapter(BaseAdapter):
    """Claude SDK adapter implementation.

    This adapter provides access to Claude through the Claude Code SDK,
    handling requests directly without delegation.
    """

    def __init__(self) -> None:
        """Initialize the Claude SDK adapter."""
        self.logger = structlog.get_logger(__name__)
        self.client: ClaudeSDKClient | None = None
        self.converter = MessageConverter()
        self.options_handler = OptionsHandler()
        self._initialized = False
        self._detection_service: Any | None = None

    def set_detection_service(self, detection_service: Any) -> None:
        """Set the detection service.

        Args:
            detection_service: Claude CLI detection service
        """
        self._detection_service = detection_service

    def set_proxy_service(self, proxy_service: Any) -> None:
        """Set the proxy service (not used in this adapter).

        Args:
            proxy_service: ProxyService instance (unused)
        """
        # Not used - we handle requests directly
        pass

    def _ensure_initialized(self) -> None:
        """Ensure adapter is properly initialized.

        Raises:
            HTTPException: If initialization fails
        """
        if self._initialized:
            return

        try:
            from ccproxy.config.settings import get_settings

            settings = get_settings()

            # Create Claude SDK client
            self.client = ClaudeSDKClient(settings=settings)

            # Update options handler with settings
            self.options_handler = OptionsHandler(settings=settings)

            self._initialized = True
            self.logger.debug("Claude SDK adapter initialized successfully")

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
        self._ensure_initialized()

        # Parse request body
        body = await request.body()
        try:
            request_data = json.loads(body) if body else {}
        except json.JSONDecodeError as e:
            raise HTTPException(
                status_code=400, detail="Invalid JSON in request body"
            ) from e

        # Check if streaming is requested
        stream = request_data.get("stream", False)

        if stream:
            # Handle as streaming
            return await self.handle_streaming(request, endpoint, **kwargs)

        # Non-streaming request
        if endpoint == "/v1/messages":
            # Native Anthropic format
            return await self._handle_anthropic_messages(request_data)
        elif endpoint == "/v1/chat/completions":
            # OpenAI format - needs conversion
            return await self._handle_openai_chat(request_data)
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
        self._ensure_initialized()

        # Parse request body
        body = await request.body()
        try:
            request_data = json.loads(body) if body else {}
        except json.JSONDecodeError as e:
            raise HTTPException(
                status_code=400, detail="Invalid JSON in request body"
            ) from e

        # Force streaming
        request_data["stream"] = True

        if endpoint == "/v1/messages":
            # Native Anthropic format streaming
            return await self._handle_anthropic_messages_streaming(request_data)
        elif endpoint == "/v1/chat/completions":
            # OpenAI format streaming - needs conversion
            return await self._handle_openai_chat_streaming(request_data)
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Endpoint {endpoint} not supported by Claude SDK plugin",
            )

    async def _handle_anthropic_messages(
        self, request_data: dict[str, Any]
    ) -> Response:
        """Handle non-streaming Anthropic messages endpoint.

        Args:
            request_data: Parsed request data

        Returns:
            JSON response
        """
        if not self.client:
            raise HTTPException(
                status_code=503, detail="Claude SDK client not initialized"
            )

        try:
            # Convert messages to SDK format
            messages = request_data.get("messages", [])
            prompt = MessageConverter.format_messages_to_prompt(messages)

            # Build Claude Code options
            model = request_data.get("model", "claude-3-5-sonnet-20241022")
            temperature = request_data.get("temperature")
            max_tokens = request_data.get("max_tokens", 4096)
            system = request_data.get("system")
            options = self.options_handler.create_options(
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                system_message=system,
            )

            # Create SDK message
            from ccproxy.models.claude_sdk import TextBlock, UserMessage

            sdk_message = UserMessage(content=[TextBlock(text=prompt)])

            # Query the SDK with correct types
            stream_handle = await self.client.query_completion(
                message=sdk_message,
                options=options,
            )

            # Collect the full response
            full_response = ""
            async for chunk in stream_handle.create_listener():
                if hasattr(chunk, "content"):
                    # Check if content is a list of blocks
                    if isinstance(chunk.content, list):
                        for block in chunk.content:
                            if hasattr(block, "text"):
                                full_response += block.text
                    elif isinstance(chunk.content, str):
                        full_response += chunk.content

            # Convert to Anthropic response format manually
            response = {
                "id": f"msg_{uuid.uuid4()}",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": full_response}],
                "model": model,
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                },
            }

            return Response(
                content=json.dumps(response),
                media_type="application/json",
            )

        except Exception as e:
            self.logger.error(f"Error handling Anthropic messages: {e}")
            raise HTTPException(
                status_code=500, detail=f"Claude SDK error: {str(e)}"
            ) from e

    async def _handle_anthropic_messages_streaming(
        self, request_data: dict[str, Any]
    ) -> StreamingResponse:
        """Handle streaming Anthropic messages endpoint.

        Args:
            request_data: Parsed request data

        Returns:
            Streaming response
        """
        if not self.client:
            raise HTTPException(
                status_code=503, detail="Claude SDK client not initialized"
            )

        async def stream_generator() -> AsyncIterator[bytes]:
            try:
                # Convert messages to SDK format
                messages = request_data.get("messages", [])
                prompt = MessageConverter.format_messages_to_prompt(messages)

                # Build Claude Code options
                model = request_data.get("model", "claude-3-5-sonnet-20241022")
                temperature = request_data.get("temperature")
                max_tokens = request_data.get("max_tokens", 4096)
                system = request_data.get("system")
                if not self.options_handler:
                    self.options_handler = OptionsHandler()
                options = self.options_handler.create_options(
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    system_message=system,
                )

                # Create SDK message
                from ccproxy.models.claude_sdk import TextBlock, UserMessage

                sdk_message = UserMessage(content=[TextBlock(text=prompt)])

                # Query the SDK
                stream_handle = await self.client.query_completion(
                    message=sdk_message,
                    options=options,
                )

                # Stream the response
                message_id = f"msg_{uuid.uuid4()}"

                # Message start
                message_start = {
                    "type": "message_start",
                    "message": {
                        "id": message_id,
                        "type": "message",
                        "role": "assistant",
                        "content": [],
                        "model": model,
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                    },
                }
                yield f"event: message_start\ndata: {json.dumps(message_start)}\n\n".encode()

                # Content block start
                block_start = {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                }
                yield f"event: content_block_start\ndata: {json.dumps(block_start)}\n\n".encode()

                # Stream content
                async for sdk_chunk in stream_handle.create_listener():
                    if hasattr(sdk_chunk, "content"):
                        text_content = ""
                        # Check if content is a list of blocks
                        if isinstance(sdk_chunk.content, list):
                            for block in sdk_chunk.content:
                                if hasattr(block, "text"):
                                    text_content += block.text
                        elif isinstance(sdk_chunk.content, str):
                            text_content = sdk_chunk.content

                        if text_content:
                            # Send text delta
                            delta_chunk = {
                                "type": "content_block_delta",
                                "index": 0,
                                "delta": {
                                    "type": "text_delta",
                                    "text": text_content,
                                },
                            }
                            yield f"event: content_block_delta\ndata: {json.dumps(delta_chunk)}\n\n".encode()

                # Send end chunks
                block_stop = {"type": "content_block_stop", "index": 0}
                yield f"event: content_block_stop\ndata: {json.dumps(block_stop)}\n\n".encode()

                message_delta = {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 0},
                }
                yield f"event: message_delta\ndata: {json.dumps(message_delta)}\n\n".encode()

                message_stop = {"type": "message_stop"}
                yield f"event: message_stop\ndata: {json.dumps(message_stop)}\n\n".encode()

            except Exception as e:
                self.logger.error(f"Error in streaming: {e}")
                error_chunk = {
                    "type": "error",
                    "error": {"type": "api_error", "message": str(e)},
                }
                yield f"event: error\ndata: {json.dumps(error_chunk)}\n\n".encode()

        return StreamingResponse(
            stream_generator(),
            media_type="text/event-stream",
        )

    async def _handle_openai_chat(self, request_data: dict[str, Any]) -> Response:
        """Handle non-streaming OpenAI chat completions endpoint.

        Args:
            request_data: Parsed request data

        Returns:
            JSON response in OpenAI format
        """
        # Convert from OpenAI to Anthropic format
        from ccproxy.adapters.openai.adapter import OpenAIAdapter

        openai_adapter = OpenAIAdapter()
        anthropic_request = await openai_adapter.adapt_request(request_data)

        # Process through Anthropic handler
        anthropic_response = await self._handle_anthropic_messages(anthropic_request)

        # Convert response back to OpenAI format
        response_data = (
            json.loads(anthropic_response.body)
            if hasattr(anthropic_response, "body")
            else anthropic_response
        )
        openai_response = await openai_adapter.adapt_response(response_data)

        return Response(
            content=json.dumps(openai_response),
            media_type="application/json",
        )

    async def _handle_openai_chat_streaming(
        self, request_data: dict[str, Any]
    ) -> StreamingResponse:
        """Handle streaming OpenAI chat completions endpoint.

        Args:
            request_data: Parsed request data

        Returns:
            Streaming response in OpenAI format
        """
        # Convert from OpenAI to Anthropic format
        from ccproxy.adapters.openai.adapter import OpenAIAdapter

        openai_adapter = OpenAIAdapter()
        anthropic_request = await openai_adapter.adapt_request(request_data)

        # Process through Anthropic streaming handler
        # but wrap to convert chunks to OpenAI format
        async def openai_stream_generator() -> AsyncIterator[bytes]:
            # Use OpenAI streaming processor to convert
            from ccproxy.adapters.openai.streaming import OpenAIStreamProcessor

            processor = OpenAIStreamProcessor(
                model=request_data.get("model", "gpt-4"), output_format="sse"
            )

            # Create async generator from anthropic stream
            async def anthropic_chunk_generator():
                anthropic_stream = await self._handle_anthropic_messages_streaming(
                    anthropic_request
                )
                async for chunk in anthropic_stream.body_iterator:
                    if isinstance(chunk, bytes):
                        # Parse SSE format
                        for line in chunk.decode().split("\n"):
                            if line.startswith("data:"):
                                data_str = line[5:].strip()
                                if data_str and data_str != "[DONE]":
                                    with contextlib.suppress(json.JSONDecodeError):
                                        yield json.loads(data_str)

            # Process through OpenAI adapter
            async for sse_chunk in processor.process_stream(
                anthropic_chunk_generator()
            ):
                if isinstance(sse_chunk, str):
                    yield sse_chunk.encode()
                else:
                    yield f"data: {json.dumps(sse_chunk)}\n\n".encode()

        return StreamingResponse(
            openai_stream_generator(),
            media_type="text/event-stream",
        )

    async def close(self) -> None:
        """Close any resources."""
        if self.client:
            await self.client.close()
