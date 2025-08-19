"""Claude API adapter implementation."""

import json
from typing import Any

import structlog
from fastapi import HTTPException, Request
from httpx import AsyncClient
from starlette.responses import Response, StreamingResponse

from ccproxy.services.adapters.base import BaseAdapter
from ccproxy.services.handler_config import HandlerConfig
from ccproxy.services.http_handler import PluginHTTPHandler

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
            proxy_service: ProxyService instance for handling requests (can be None initially)
            auth_manager: Authentication manager for credentials
            detection_service: Detection service for Claude CLI detection
            http_client: Optional HTTP client for making requests
            logger: Optional structured logger instance
        """
        self.http_client = http_client
        self.logger = logger or structlog.get_logger(__name__)
        self.proxy_service = proxy_service
        self._auth_manager = auth_manager
        self._detection_service = detection_service

        # Initialize OpenAI adapter for format conversion
        from ccproxy.adapters.openai.adapter import OpenAIAdapter

        self.openai_adapter = OpenAIAdapter()

        # Initialize HTTP handler and transformers (will be completed in set_proxy_service if needed)
        self._http_handler: PluginHTTPHandler | None = None
        self._request_transformer: ClaudeAPIRequestTransformer | None = None
        self._response_transformer: ClaudeAPIResponseTransformer | None = None

        # Complete initialization if proxy_service is available
        if proxy_service:
            self._complete_initialization()

    def _complete_initialization(self) -> None:
        """Complete initialization with proxy_service dependencies."""
        if not self.proxy_service:
            return

        # Initialize HTTP handler with shared HTTP client from proxy service
        shared_client = getattr(self.proxy_service, "http_client", None)
        if shared_client:
            self._http_handler = PluginHTTPHandler(http_client=shared_client)
        else:
            # Fallback to legacy config-based client
            client_config = self.proxy_service.config.get_httpx_client_config()
            self._http_handler = PluginHTTPHandler(client_config)

        # Initialize transformers with detection service
        from .transformers import (
            ClaudeAPIRequestTransformer,
            ClaudeAPIResponseTransformer,
        )

        self._request_transformer = ClaudeAPIRequestTransformer(self._detection_service)

        # Initialize response transformer with CORS settings
        cors_settings = getattr(self.proxy_service.config, "cors", None)
        self._response_transformer = ClaudeAPIResponseTransformer(cors_settings)

    def set_proxy_service(self, proxy_service: Any) -> None:
        """Set the proxy service and complete initialization.

        DEPRECATED: This method is deprecated. ProxyService should be passed
        to the constructor instead to avoid the anti-pattern of delayed initialization.

        Args:
            proxy_service: ProxyService instance for handling requests
        """
        if self.proxy_service is None:
            self.proxy_service = proxy_service
            self._complete_initialization()
        # If already set via constructor, ignore this call

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

        # Read request body
        body = await request.body()

        # Get authentication headers
        if not self._auth_manager:
            raise HTTPException(
                status_code=503, detail="Authentication manager not available"
            )
        auth_headers = await self._auth_manager.get_auth_headers()

        # Extract access_token (x-api-key for Anthropic)
        access_token = auth_headers.get("x-api-key") if auth_headers else None

        # Determine target URL and format conversion needs
        if endpoint.endswith("/v1/messages"):
            # Native Anthropic format - no conversion needed
            target_url = "https://api.anthropic.com/v1/messages"
            needs_conversion = False
        elif endpoint.endswith("/v1/chat/completions"):
            # OpenAI format - needs conversion to Anthropic messages format
            target_url = "https://api.anthropic.com/v1/messages"
            needs_conversion = True
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Endpoint {endpoint} not supported by Claude API plugin",
            )

        # Create simplified provider context
        handler_config = HandlerConfig(
            request_adapter=self.openai_adapter if needs_conversion else None,
            response_adapter=self.openai_adapter if needs_conversion else None,
            request_transformer=self._request_transformer,
            response_transformer=self._response_transformer,
            supports_streaming=True,
        )

        # Prepare request using HTTP handler
        if not self._http_handler:
            raise HTTPException(status_code=503, detail="HTTP handler not initialized")

        (
            transformed_body,
            headers,
            is_streaming,
        ) = await self._http_handler.prepare_request(
            request_body=body,
            handler_config=handler_config,
            auth_headers=auth_headers,
            request_headers=dict(request.headers),
            access_token=access_token,
        )

        self.logger.info(
            "claude_api_request",
            endpoint=endpoint,
            target_url=target_url,
            needs_conversion=needs_conversion,
            is_streaming=is_streaming,
        )

        # Make the actual HTTP request using the shared handler
        if not self.proxy_service:
            raise HTTPException(status_code=503, detail="Proxy service not available")

        return await self._http_handler.handle_request(
            method=method,
            url=target_url,
            headers=headers,
            body=transformed_body,
            handler_config=handler_config,
            is_streaming=is_streaming,
            streaming_handler=self.proxy_service.streaming_handler
            if is_streaming
            else None,
            request_context={},
        )

    async def handle_streaming(
        self, request: Request, endpoint: str, **kwargs: Any
    ) -> StreamingResponse:
        """Handle a streaming request to the Claude API.

        Args:
            request: FastAPI request object
            endpoint: Target endpoint path
            **kwargs: Additional arguments

        Returns:
            Streaming response from Claude API
        """

        # Ensure the request has stream=true
        body = await request.body()
        try:
            request_data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            request_data = {}

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
            return StreamingResponse(
                iter([result.body if hasattr(result, "body") else b""]),
                media_type="text/event-stream",
            )

        return result

    async def cleanup(self) -> None:
        """Cleanup resources when shutting down."""
        try:
            # Cleanup HTTP handler if it exists
            if self._http_handler:
                if hasattr(self._http_handler, "cleanup"):
                    await self._http_handler.cleanup()
                self._http_handler = None

            # Close any dedicated HTTP client if we're using one
            if self.http_client:
                try:
                    await self.http_client.aclose()
                    self.http_client = None
                except Exception as e:
                    self.logger.warning(
                        "claude_api_http_client_close_failed",
                        error=str(e),
                        exc_info=e,
                    )

            # Clear references to prevent memory leaks
            self.proxy_service = None
            self._request_transformer = None
            self._response_transformer = None

            self.logger.debug("claude_api_adapter_cleanup_completed")

        except Exception as e:
            self.logger.error(
                "claude_api_adapter_cleanup_failed",
                error=str(e),
                exc_info=e,
            )
