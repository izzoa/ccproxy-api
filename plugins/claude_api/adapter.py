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
        if http_client:
            self._http_handler: PluginHTTPHandler = PluginHTTPHandler(
                http_client=http_client
            )
        elif proxy_service and hasattr(proxy_service, "http_client"):
            self._http_handler = PluginHTTPHandler(
                http_client=proxy_service.http_client
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

        self.logger.info(
            "claude_api_request",
            endpoint=endpoint,
            target_url=target_url,
            needs_conversion=needs_conversion,
            is_streaming=is_streaming,
        )

        # Get streaming handler if needed
        streaming_handler = None
        if is_streaming and self.proxy_service:
            streaming_handler = getattr(self.proxy_service, "streaming_handler", None)

        # Execute request
        return await self._http_handler.handle_request(
            method=method,
            url=target_url,
            headers=headers,
            body=transformed_body,
            handler_config=handler_config,
            is_streaming=is_streaming,
            streaming_handler=streaming_handler,
            request_context={},
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
