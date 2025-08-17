"""Claude API adapter implementation."""

import json
from typing import Any

import structlog
from fastapi import HTTPException, Request
from httpx import AsyncClient
from starlette.responses import Response, StreamingResponse

from ccproxy.services.adapters.base import BaseAdapter
from ccproxy.services.http_handler import PluginHTTPHandler
from ccproxy.services.provider_context import ProviderContext


logger = structlog.get_logger(__name__)


class ClaudeAPIAdapter(BaseAdapter):
    """Claude API adapter implementation.

    This adapter provides direct access to the Anthropic Claude API
    with support for both native Anthropic format and OpenAI-compatible format.
    """

    def __init__(
        self,
        http_client: AsyncClient | None = None,
        logger: structlog.BoundLogger | None = None,
    ) -> None:
        """Initialize the Claude API adapter.

        Args:
            http_client: Optional HTTP client for making requests
            logger: Optional structured logger instance
        """
        self.http_client = http_client
        self.logger = logger or structlog.get_logger(__name__)
        self.proxy_service: Any | None = None
        self.openai_adapter: Any | None = None
        self._initialized = False
        self._http_handler: PluginHTTPHandler | None = None
        self._auth_manager: Any | None = None
        self._request_transformer: Any | None = None
        self._response_transformer: Any | None = None
        self._detection_service: Any | None = None

    def set_proxy_service(self, proxy_service: Any) -> None:
        """Set the proxy service for request handling.

        Args:
            proxy_service: ProxyService instance for handling requests
        """
        self.proxy_service = proxy_service

    def set_openai_adapter(self, adapter: Any) -> None:
        """Set the OpenAI adapter for format conversion.

        Args:
            adapter: OpenAI adapter for format conversion
        """
        self.openai_adapter = adapter

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

            # Create OpenAI adapter for format conversion if not set
            if not self.openai_adapter:
                from ccproxy.adapters.openai.adapter import OpenAIAdapter

                self.openai_adapter = OpenAIAdapter()

            # Initialize HTTP handler with client config from proxy service
            if not self._http_handler and self.proxy_service:
                client_config = self.proxy_service.config.get_httpx_client_config()
                self._http_handler = PluginHTTPHandler(client_config)

            # Get auth manager from proxy service (credentials manager)
            if not self._auth_manager and self.proxy_service:
                self._auth_manager = self.proxy_service.credentials_manager

            # Initialize transformers with detection service
            if not self._request_transformer and hasattr(
                self.proxy_service, "plugin_manager"
            ):
                plugin = self.proxy_service.plugin_manager.plugin_registry.get_plugin(
                    "claude_api"
                )
                if plugin and hasattr(plugin, "_detection_service"):
                    self._detection_service = plugin._detection_service

                    # Create transformers with detection service
                    from .transformers import (
                        ClaudeAPIRequestTransformer,
                        ClaudeAPIResponseTransformer,
                    )

                    self._request_transformer = ClaudeAPIRequestTransformer(
                        self._detection_service
                    )
                    self._response_transformer = ClaudeAPIResponseTransformer()

            self._initialized = True
            self.logger.debug("claude_api_adapter_initialized")

        except Exception as e:
            self.logger.error(
                "claude_api_adapter_initialization_failed",
                error=str(e),
            )
            raise HTTPException(
                status_code=503, detail=f"Claude API initialization failed: {str(e)}"
            ) from e

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
        self._ensure_initialized(request)

        # Read request body
        body = await request.body()

        # Get authentication headers
        if not self._auth_manager:
            raise HTTPException(
                status_code=503, detail="Authentication manager not available"
            )
        auth_headers = await self._auth_manager.get_auth_headers()

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

        # Create provider context with transformers
        provider_context = ProviderContext(
            provider_name="claude-api",
            auth_manager=self._auth_manager,
            target_base_url="https://api.anthropic.com",
            request_adapter=self.openai_adapter if needs_conversion else None,
            response_adapter=self.openai_adapter if needs_conversion else None,
            request_transformer=self._request_transformer,
            response_transformer=self._response_transformer,
            supports_streaming=True,
            requires_session=False,
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
            provider_context=provider_context,
            auth_headers=auth_headers,
            request_headers=dict(request.headers),
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
            provider_context=provider_context,
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
        self._ensure_initialized(request)

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
        self._initialized = False
        self.logger.debug("claude_api_adapter_cleanup_completed")
