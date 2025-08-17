"""Simplified Codex adapter using delegation pattern."""

import contextlib
import json
import uuid
from typing import Any

import structlog
from fastapi import HTTPException, Request
from starlette.responses import Response, StreamingResponse

from ccproxy.auth.base import AuthManager
from ccproxy.services.adapters.base import BaseAdapter
from ccproxy.services.http_handler import PluginHTTPHandler
from ccproxy.services.provider_context import ProviderContext
from ccproxy.services.proxy_service import ProxyService

from .format_adapter import CodexFormatAdapter
from .transformers import CodexRequestTransformer, CodexResponseTransformer


logger = structlog.get_logger(__name__)


class CodexAdapter(BaseAdapter):
    """Codex adapter using ProxyService delegation pattern.

    This adapter follows the same pattern as Claude API adapter,
    delegating actual HTTP operations to ProxyService.
    """

    def __init__(
        self,
        http_client: Any | None = None,
        logger: structlog.BoundLogger | None = None,
    ):
        """Initialize the Codex adapter.

        Args:
            http_client: Not used directly (for interface compatibility)
            logger: Structured logger instance
        """
        self.logger = logger or structlog.get_logger(__name__)
        self.proxy_service: ProxyService | None = None
        self.format_adapter: CodexFormatAdapter | None = None
        self.request_transformer: CodexRequestTransformer | None = None
        self.response_transformer: CodexResponseTransformer | None = None
        self._initialized = False
        self._auth_manager: AuthManager | None = None
        self._detection_service = None
        self._http_handler: PluginHTTPHandler | None = None

    def set_proxy_service(self, proxy_service: ProxyService) -> None:
        """Set the proxy service for request handling."""
        self.proxy_service = proxy_service

    def set_auth_manager(self, auth_manager: AuthManager) -> None:
        """Set the authentication manager."""
        self._auth_manager = auth_manager

    def set_detection_service(self, detection_service: Any) -> None:
        """Set the detection service."""
        self._detection_service = detection_service
        if self.request_transformer:
            self.request_transformer.detection_service = detection_service

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
            if not self.format_adapter:
                self.format_adapter = CodexFormatAdapter()

            if not self.request_transformer:
                self.request_transformer = CodexRequestTransformer(
                    self._detection_service
                )

            if not self.response_transformer:
                self.response_transformer = CodexResponseTransformer()

            # Initialize HTTP handler with client config from proxy service
            if not self._http_handler and self.proxy_service:
                client_config = self.proxy_service.config.get_httpx_client_config()
                self._http_handler = PluginHTTPHandler(client_config)

            self._initialized = True
            self.logger.debug("codex_adapter_initialized")

        except Exception as e:
            self.logger.error(
                "codex_adapter_initialization_failed",
                error=str(e),
            )
            raise HTTPException(
                status_code=503, detail=f"Codex initialization failed: {str(e)}"
            ) from e

    async def handle_request(
        self, request: Request, endpoint: str, method: str, **kwargs: Any
    ) -> Response | StreamingResponse:
        """Handle a request to the Codex API.

        Args:
            request: FastAPI request object
            endpoint: Target endpoint path
            method: HTTP method
            **kwargs: Additional arguments (e.g., session_id)

        Returns:
            Response from Codex API
        """
        self._ensure_initialized(request)

        # Extract session_id
        session_id = kwargs.get("session_id") or str(uuid.uuid4())

        # Read request body
        body = await request.body()

        # Check if format conversion is needed
        needs_conversion = False
        if body:
            try:
                request_data = json.loads(body)
                needs_conversion = "messages" in request_data
            except json.JSONDecodeError:
                pass

        # Get authentication headers
        if not self._auth_manager:
            raise HTTPException(
                status_code=503, detail="Authentication manager not available"
            )
        auth_headers = await self._auth_manager.get_auth_headers()

        # Build target URL
        target_url = "https://chatgpt.com/backend-api/codex/responses"

        # Create provider context
        context = ProviderContext(
            provider_name="codex",
            auth_manager=self._auth_manager,
            target_base_url="https://chatgpt.com",
            route_prefix="/codex",
            request_adapter=self.format_adapter if needs_conversion else None,
            response_adapter=self.format_adapter if needs_conversion else None,
            request_transformer=self.request_transformer,
            response_transformer=self.response_transformer,
            supports_streaming=True,
            requires_session=True,
            session_id=session_id,
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
            provider_context=context,
            auth_headers=auth_headers,
            request_headers=dict(request.headers),
            session_id=session_id,
        )

        self.logger.info(
            "codex_request",
            session_id=session_id,
            needs_conversion=needs_conversion,
            endpoint=endpoint,
            is_streaming=is_streaming,
            target_url=target_url,
        )

        # Make the actual HTTP request using the shared handler
        if not self.proxy_service:
            raise HTTPException(status_code=503, detail="Proxy service not available")

        return await self._http_handler.handle_request(
            method=method,
            url=target_url,
            headers=headers,
            body=transformed_body,
            provider_context=context,
            is_streaming=is_streaming,
            streaming_handler=self.proxy_service.streaming_handler
            if is_streaming
            else None,
            request_context={},
        )

    async def handle_streaming(
        self, request: Request, endpoint: str, **kwargs: Any
    ) -> StreamingResponse:
        """Handle a streaming request to the Codex API.

        Args:
            request: FastAPI request object
            endpoint: Target endpoint path
            **kwargs: Additional arguments

        Returns:
            Streaming response from Codex API
        """
        self._ensure_initialized(request)

        # Ensure stream=true in request body
        body = await request.body()
        request_data = {}
        if body:
            with contextlib.suppress(json.JSONDecodeError):
                request_data = json.loads(body)

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
        self.logger.debug("codex_adapter_cleaned_up")
