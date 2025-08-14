"""Codex adapter implementation for the plugin system."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import structlog
from fastapi import Request
from starlette.responses import Response, StreamingResponse

from ccproxy.adapters.openai.codex_adapter import CodexAdapter as CoreCodexAdapter
from ccproxy.services.adapters.base import BaseAdapter


logger = structlog.get_logger(__name__)


class CodexAdapter(BaseAdapter):
    """Codex adapter for the plugin system.

    This adapter wraps the core CodexAdapter functionality and provides
    the interface required by the plugin system's BaseAdapter protocol.
    """

    def __init__(self, http_client: httpx.AsyncClient, logger: structlog.BoundLogger):
        """Initialize the Codex adapter."""
        super().__init__()
        self._http_client = http_client
        self._logger = logger
        self._core_adapter = CoreCodexAdapter()

    async def handle_request(
        self, request: Request, endpoint: str, method: str, **kwargs: Any
    ) -> Response:
        """Handle a provider-specific request.

        For Codex, most requests are streaming, but this handles direct API calls.
        """
        self._logger.debug(
            "codex_adapter_handle_request",
            endpoint=endpoint,
            method=method,
            has_kwargs=bool(kwargs),
        )

        try:
            # Get request body
            body = await request.body()
            if body:
                import json
                request_data = json.loads(body)
            else:
                request_data = {}

            # Transform using core adapter
            transformed_request = await self._core_adapter.adapt_request(request_data)

            # For non-streaming, we'd typically make an HTTP request here
            # But Codex primarily uses streaming, so return appropriate response
            return Response(
                content='{"error": "Non-streaming Codex requests not supported"}',
                status_code=400,
                media_type="application/json",
            )
        except Exception as e:
            self._logger.error("codex_adapter_request_error", error=str(e))
            return Response(
                content=f'{{"error": "Request processing failed: {str(e)}"}}',
                status_code=500,
                media_type="application/json",
            )

    async def handle_streaming(
        self, request: Request, endpoint: str, **kwargs: Any
    ) -> StreamingResponse:
        """Handle a streaming request.

        This implements basic streaming functionality using the core adapter.
        """
        self._logger.debug(
            "codex_adapter_handle_streaming",
            endpoint=endpoint,
            has_kwargs=bool(kwargs),
        )

        async def stream_generator() -> AsyncIterator[str]:
            try:
                # Get request body
                body = await request.body()
                if body:
                    import json
                    request_data = json.loads(body)
                else:
                    request_data = {}

                # Transform using core adapter
                transformed_request = await self._core_adapter.adapt_request(request_data)

                # For actual implementation, we'd make HTTP request to Codex API
                # and stream the response. For now, return a structured response.
                yield "data: " + json.dumps({
                    "id": f"codex-{hash(str(transformed_request)) % 10000}",
                    "object": "chat.completion.chunk",
                    "created": int(__import__("time").time()),
                    "model": request_data.get("model", "gpt-4"),
                    "choices": [{
                        "index": 0,
                        "delta": {"content": "Codex streaming response placeholder"},
                        "finish_reason": None
                    }]
                }) + "\n\n"

                yield "data: " + json.dumps({
                    "id": f"codex-{hash(str(transformed_request)) % 10000}",
                    "object": "chat.completion.chunk",
                    "created": int(__import__("time").time()),
                    "model": request_data.get("model", "gpt-4"),
                    "choices": [{
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop"
                    }]
                }) + "\n\n"

                yield "data: [DONE]\n\n"

            except Exception as e:
                self._logger.error("codex_adapter_streaming_error", error=str(e))
                yield f"data: {{'error': 'Streaming failed: {str(e)}'}}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(stream_generator(), media_type="text/event-stream")

    async def validate_request(
        self, request: Request, endpoint: str
    ) -> dict[str, Any] | None:
        """Validate request before processing.

        Args:
            request: FastAPI request object
            endpoint: Target endpoint path

        Returns:
            Validation result or None if valid
        """
        self._logger.debug(
            "codex_adapter_validate_request",
            endpoint=endpoint,
            method=request.method,
        )
        # Basic validation - could be extended
        return None

    async def transform_request(self, request_data: dict[str, Any]) -> dict[str, Any]:
        """Transform request data using the core adapter.

        Args:
            request_data: Original request data

        Returns:
            Transformed request data
        """
        self._logger.debug(
            "codex_adapter_transform_request",
            has_messages=bool(request_data.get("messages")),
            model=request_data.get("model"),
        )
        return await self._core_adapter.adapt_request(request_data)

    async def transform_response(self, response_data: dict[str, Any]) -> dict[str, Any]:
        """Transform response data using the core adapter.

        Args:
            response_data: Original response data

        Returns:
            Transformed response data
        """
        self._logger.debug(
            "codex_adapter_transform_response",
            response_id=response_data.get("id"),
            has_output=bool(response_data.get("output")),
        )
        return await self._core_adapter.adapt_response(response_data)

    async def cleanup(self) -> None:
        """Cleanup resources."""
        self._logger.debug("codex_adapter_cleanup")
        # No special cleanup needed for this adapter

    # Additional methods that wrap the core adapter for compatibility

    async def adapt_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Convert a request from OpenAI to Codex format."""
        return await self._core_adapter.adapt_request(request)

    async def adapt_response(self, response: dict[str, Any]) -> dict[str, Any]:
        """Convert a response from Codex to OpenAI format."""
        return await self._core_adapter.adapt_response(response)

    async def adapt_stream(
        self, stream: AsyncIterator[dict[str, Any]]
    ) -> AsyncIterator[dict[str, Any]]:
        """Convert a streaming response from Codex to OpenAI format."""
        async for chunk in self._core_adapter.adapt_stream(stream):
            yield chunk

    def convert_chat_to_response_request(
        self, chat_request: dict[str, Any]
    ) -> dict[str, Any]:
        """Convert Chat Completions request to Response API format."""
        return self._core_adapter.convert_chat_to_response_request(chat_request)

    def convert_response_to_chat(self, response_data: dict[str, Any]) -> dict[str, Any]:
        """Convert Response API response to Chat Completions format."""
        return self._core_adapter.convert_response_to_chat(response_data)

    async def convert_response_stream_to_chat(
        self, response_stream: AsyncIterator[bytes]
    ) -> AsyncIterator[dict[str, Any]]:
        """Convert Response API SSE stream to Chat Completions format."""
        async for chunk in self._core_adapter.convert_response_stream_to_chat(
            response_stream
        ):
            yield chunk

    def convert_error_to_chat_format(
        self, error_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Convert Response API error to Chat Completions error format."""
        return self._core_adapter.convert_error_to_chat_format(error_data)

    # Provide access to the core adapter for direct use when needed
    def get_core_adapter(self) -> CoreCodexAdapter:
        """Get the underlying core Codex adapter.

        This allows direct access to the core adapter when the plugin
        system needs to use it directly with ProviderContext.
        """
        return self._core_adapter
