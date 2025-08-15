"""Claude API adapter implementation."""

from typing import Any

import structlog
from fastapi import HTTPException, Request
from httpx import AsyncClient
from starlette.responses import Response, StreamingResponse

from ccproxy.services.adapters.base import BaseAdapter


logger = structlog.get_logger(__name__)


class ClaudeAPIAdapter(BaseAdapter):
    """Claude API adapter implementation.
    
    This adapter provides direct access to the Anthropic Claude API
    with support for both native Anthropic format and OpenAI-compatible format.
    """
    
    def __init__(
        self, 
        http_client: AsyncClient | None = None, 
        logger: structlog.BoundLogger | None = None
    ) -> None:
        """Initialize the Claude API adapter.
        
        Args:
            http_client: Optional HTTP client for making requests
            logger: Optional structured logger instance
        """
        self.http_client = http_client
        self.logger = logger or structlog.get_logger(__name__)
        self.proxy_service = None
        self.openai_adapter = None
        self._initialized = False
        
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
                        status_code=503,
                        detail="Proxy service not available"
                    )
                self.proxy_service = proxy_service
            
            # Create OpenAI adapter for format conversion if not set
            if not self.openai_adapter:
                from ccproxy.adapters.openai.adapter import OpenAIAdapter
                self.openai_adapter = OpenAIAdapter()
            
            self._initialized = True
            self.logger.debug("Claude API adapter initialized successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize Claude API adapter: {e}")
            raise HTTPException(
                status_code=503,
                detail=f"Claude API initialization failed: {str(e)}"
            )
    
    async def handle_request(
        self, request: Request, endpoint: str, method: str, **kwargs: Any
    ) -> Response:
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
        
        # Create provider context based on endpoint
        from ccproxy.services.provider_context import ProviderContext
        
        if endpoint == "/v1/messages":
            # Native Anthropic format - no conversion needed
            provider_context = ProviderContext(
                provider_name="claude-api-native",
                auth_manager=self.proxy_service.credentials_manager,
                target_base_url="https://api.anthropic.com",
                request_adapter=None,  # No conversion needed
                response_adapter=None,  # Pass through
                supports_streaming=True,
                requires_session=False,
            )
        elif endpoint == "/v1/chat/completions":
            # OpenAI format - needs conversion
            provider_context = ProviderContext(
                provider_name="claude-api-openai",
                auth_manager=self.proxy_service.credentials_manager,
                target_base_url="https://api.anthropic.com",
                request_adapter=self.openai_adapter,
                response_adapter=self.openai_adapter,
                supports_streaming=True,
                requires_session=False,
            )
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Endpoint {endpoint} not supported by Claude API plugin"
            )
        
        # Dispatch request through proxy service
        result = await self.proxy_service.dispatch_request(request, provider_context)
        
        # Handle different response types
        if isinstance(result, StreamingResponse):
            # Check if the original request wanted streaming
            import json
            
            body = await request.body()
            try:
                request_data = json.loads(body) if body else {}
                if request_data.get("stream", False):
                    # Return the streaming response as-is
                    return result
            except json.JSONDecodeError:
                pass
            
            # For non-streaming requests, return the streaming response
            # The proxy service will handle the conversion
            return result
        
        return result
    
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
        import json
        
        body = await request.body()
        try:
            request_data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            request_data = {}
        
        # Force streaming
        request_data["stream"] = True
        
        # Create a new request with modified body
        # We need to update the request body for streaming
        modified_body = json.dumps(request_data).encode()
        
        # Create new request scope with updated body
        modified_scope = {
            **request.scope,
            "_body": modified_body,
        }
        
        # Create modified request
        from starlette.requests import Request as StarletteRequest
        modified_request = StarletteRequest(
            scope=modified_scope,
            receive=request.receive,
        )
        
        # Set the body on the request
        modified_request._body = modified_body
        
        # Create provider context based on endpoint
        from ccproxy.services.provider_context import ProviderContext
        
        if endpoint == "/v1/messages":
            # Native Anthropic format
            provider_context = ProviderContext(
                provider_name="claude-api-native",
                auth_manager=self.proxy_service.credentials_manager,
                target_base_url="https://api.anthropic.com",
                request_adapter=None,
                response_adapter=None,
                supports_streaming=True,
                requires_session=False,
            )
        elif endpoint == "/v1/chat/completions":
            # OpenAI format
            provider_context = ProviderContext(
                provider_name="claude-api-openai",
                auth_manager=self.proxy_service.credentials_manager,
                target_base_url="https://api.anthropic.com",
                request_adapter=self.openai_adapter,
                response_adapter=self.openai_adapter,
                supports_streaming=True,
                requires_session=False,
            )
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Streaming not supported for endpoint {endpoint}"
            )
        
        # Dispatch request through proxy service
        result = await self.proxy_service.dispatch_request(
            modified_request, provider_context
        )
        
        # Ensure we got a streaming response
        if not isinstance(result, StreamingResponse):
            # Convert to streaming response
            return StreamingResponse(
                iter([result.body if hasattr(result, "body") else b""]),
                media_type=result.media_type
                if hasattr(result, "media_type")
                else "application/json",
                status_code=result.status_code
                if hasattr(result, "status_code")
                else 200,
            )
        
        return result
    
    async def cleanup(self) -> None:
        """Cleanup resources when shutting down."""
        self._initialized = False
        self.logger.debug("Claude API adapter cleaned up")