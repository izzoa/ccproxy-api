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

            self._initialized = True
            self.logger.debug("codex_adapter_initialized")

        except Exception as e:
            self.logger.error(f"Failed to initialize Codex adapter: {e}")
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

        # Check if format conversion is needed
        body = await request.body()
        needs_conversion = False
        if body:
            try:
                request_data = json.loads(body)
                needs_conversion = "messages" in request_data
            except json.JSONDecodeError:
                pass

        # Create provider context
        if not self._auth_manager:
            raise HTTPException(
                status_code=503, detail="Authentication manager not available"
            )

        context = ProviderContext(
            provider_name="codex",
            auth_manager=self._auth_manager,
            target_base_url="https://chatgpt.com",
            route_prefix="/codex",
            request_adapter=self.format_adapter if needs_conversion else None,
            response_adapter=self.format_adapter if needs_conversion else None,
            # Object-based transformers with transform_headers/transform_body methods
            request_transformer=self.request_transformer,
            response_transformer=self.response_transformer,
            supports_streaming=True,
            requires_session=True,
            session_id=session_id,
            path_transformer=lambda p: "/backend-api/codex/responses",
        )

        self.logger.info(
            "codex_request_delegation",
            session_id=session_id,
            needs_conversion=needs_conversion,
            endpoint=endpoint,
        )

        # Delegate to proxy service
        if not self.proxy_service:
            raise HTTPException(status_code=503, detail="Proxy service not available")
        return await self.proxy_service.dispatch_request(request, context)

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

        # Delegate to handle_request which will handle streaming via ProxyService
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
