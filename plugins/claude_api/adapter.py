"""Claude API adapter implementation."""

import json
import time
from typing import Any

from fastapi import HTTPException, Request
from httpx import AsyncClient
from starlette.responses import Response, StreamingResponse

from ccproxy.adapters.openai.adapter import OpenAIAdapter
from ccproxy.config.constants import (
    CLAUDE_API_BASE_URL,
    CLAUDE_MESSAGES_ENDPOINT,
    OPENAI_CHAT_COMPLETIONS_PATH,
)
from ccproxy.core.logging import get_plugin_logger
from ccproxy.services.adapters.base import BaseAdapter
from ccproxy.services.handler_config import HandlerConfig
from ccproxy.streaming.deferred_streaming import DeferredStreaming


logger = get_plugin_logger()


class ClaudeAPIAdapter(BaseAdapter):
    """Consolidated Claude API adapter with all transformations."""

    def __init__(
        self,
        auth_manager: Any,
        detection_service: Any,
        http_client: AsyncClient,
        logger: Any | None = None,
        context: dict[str, Any] | None = None,
    ):
        """Initialize Claude API adapter."""
        self.auth_manager = auth_manager
        self.detection_service = detection_service
        self.http_client = http_client
        self.logger = logger or get_plugin_logger()
        self.context = context or {}

        # Initialize format converter
        self.openai_adapter = OpenAIAdapter()

        # Get injection mode from context
        self.injection_mode = "minimal"  # default
        if context:
            config = context.get("config")
            if config:
                self.injection_mode = getattr(
                    config, "system_prompt_injection_mode", "minimal"
                )

    async def handle_request(
        self, request: Request, endpoint: str, method: str, **kwargs: Any
    ) -> Response | StreamingResponse | DeferredStreaming:
        """Handle a request to the Claude API."""
        # Resolve endpoint
        target_url = await self._resolve_endpoint(endpoint, request)

        # Create handler configuration
        config = await self._create_handler_config(request, endpoint, method)

        # Transform request
        transformed_request = await self._transform_request(request, config)

        # Make the HTTP request
        response = await self.http_client.request(
            method=transformed_request["method"],
            url=transformed_request["url"],
            headers=transformed_request["headers"],
            json=transformed_request["json"],
        )

        # Transform response if needed
        if config.response_adapter:
            response_json = response.json()
            converted_response = await config.response_adapter.adapt_response(
                response_json
            )
            return Response(
                content=json.dumps(converted_response),
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type="application/json",
            )

        # Return response as-is
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.headers.get("content-type", "application/json"),
        )

    async def handle_streaming(
        self, request: Request, endpoint: str, **kwargs: Any
    ) -> StreamingResponse | DeferredStreaming:
        """Handle a streaming request to the Claude API."""
        # Force streaming in the request
        body = await request.json()
        body["stream"] = True

        # Resolve endpoint
        target_url = await self._resolve_endpoint(endpoint, request)

        # Transform headers
        headers = await self._transform_headers(dict(request.headers))

        # Check if we need format conversion
        needs_conversion = OPENAI_CHAT_COMPLETIONS_PATH in endpoint
        if needs_conversion:
            body = await self.openai_adapter.adapt_request(body)

        # Make streaming request
        response = await self.http_client.stream(
            "POST", target_url, headers=headers, json=body
        )

        # Return streaming response
        return StreamingResponse(
            response.aiter_bytes(),
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type="text/event-stream",
        )

    async def cleanup(self) -> None:
        """Cleanup adapter resources."""
        # No cleanup needed for this adapter
        pass

    async def _resolve_endpoint(self, endpoint: str, request: Request) -> str:
        """Resolve Claude API endpoint."""
        # Handle OpenAI compatibility endpoint
        if endpoint == OPENAI_CHAT_COMPLETIONS_PATH:
            return f"{CLAUDE_API_BASE_URL}{CLAUDE_MESSAGES_ENDPOINT}"

        # Remove /api prefix if present (from route prefix)
        if endpoint.startswith("/api/"):
            endpoint = endpoint[4:]  # Remove "/api" prefix
        
        # Standard Claude endpoint
        if not endpoint.startswith("http"):
            return f"{CLAUDE_API_BASE_URL}{endpoint}"

        return endpoint

    async def _create_handler_config(
        self, request: Request, endpoint: str, method: str
    ) -> HandlerConfig:
        """Create handler configuration for Claude API."""
        # Detect if OpenAI format conversion is needed
        is_openai_format = OPENAI_CHAT_COMPLETIONS_PATH in request.url.path

        # Create adapters if format conversion is needed
        request_adapter = self.openai_adapter if is_openai_format else None
        response_adapter = self.openai_adapter if is_openai_format else None

        return HandlerConfig(
            request_adapter=request_adapter,
            response_adapter=response_adapter,
            supports_streaming=True,
        )

    async def _transform_headers(self, headers: dict[str, str]) -> dict[str, str]:
        """Transform headers for Claude API.

        This consolidates the logic from transformers/request.py
        """
        # Get auth headers from auth manager
        auth_headers = await self.auth_manager.get_auth_headers()

        # Build transformed headers
        transformed = {
            **headers,
            **auth_headers,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "messages-2023-12-15",
        }

        # Remove any OpenAI-specific headers
        transformed.pop("openai-beta", None)
        transformed.pop("authorization", None)  # Will be replaced by auth_headers
        
        # Remove Content-Length as httpx will calculate it when using json=
        transformed.pop("content-length", None)
        
        # Remove host header as httpx will set it
        transformed.pop("host", None)

        return transformed

    async def _transform_request(
        self, request: Request, config: HandlerConfig
    ) -> dict[str, Any]:
        """Transform request for Claude API execution."""
        try:
            body = await request.json()
        except Exception:
            body = {}

        # Transform headers
        headers = await self._transform_headers(dict(request.headers))

        # Check if format conversion is needed
        if config.request_adapter:
            # Convert from OpenAI to Claude format using the adapter
            body = await config.request_adapter.adapt_request(body)

        # Get the resolved endpoint
        endpoint_url = await self._resolve_endpoint(request.url.path, request)

        return {
            "method": "POST",
            "url": endpoint_url,
            "headers": headers,
            "json": body,
        }

    def _convert_openai_to_claude(self, openai_body: dict[str, Any]) -> dict[str, Any]:
        """Convert OpenAI format to Claude format.

        This consolidates the logic from format_adapter.py
        """
        # Format conversion logic
        messages = openai_body.get("messages", [])
        claude_messages = []

        for msg in messages:
            role = msg["role"]
            if role == "system":
                # Handle system messages separately
                continue
            claude_messages.append(
                {
                    "role": "assistant" if role == "assistant" else "user",
                    "content": msg["content"],
                }
            )

        return {
            "messages": claude_messages,
            "model": openai_body.get("model", "claude-3-opus-20240229"),
            "max_tokens": openai_body.get("max_tokens", 4096),
            "stream": openai_body.get("stream", False),
            "temperature": openai_body.get("temperature", 1.0),
        }

    def _convert_claude_to_openai(self, claude_body: dict[str, Any]) -> dict[str, Any]:
        """Convert Claude format to OpenAI format.

        This consolidates the logic from format_adapter.py
        """
        # Format conversion logic
        return {
            "id": claude_body.get("id", ""),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": claude_body.get("model", ""),
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": claude_body.get("content", [{}])[0].get("text", ""),
                    },
                    "finish_reason": claude_body.get("stop_reason", "stop"),
                }
            ],
            "usage": claude_body.get("usage", {}),
        }
