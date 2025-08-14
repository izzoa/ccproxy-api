"""Claude API provider plugin."""

from typing import Any

import structlog
from fastapi import HTTPException, Request
from starlette.responses import Response, StreamingResponse

from ccproxy.models.provider import ProviderConfig
from ccproxy.services.adapters.base import BaseAdapter


logger = structlog.get_logger(__name__)


class ClaudeAPIAdapter(BaseAdapter):
    """Claude API adapter implementation.

    This plugin provides direct access to the Anthropic Claude API
    using the proxy service for proper request handling.
    """

    def __init__(self) -> None:
        """Initialize the Claude API adapter."""
        self.proxy_service = None
        self.openai_adapter = None
        self._initialized = False

    def _lazy_init(self, request: Request) -> None:
        """Lazy initialization to avoid import issues during plugin discovery."""
        if self._initialized:
            return

        try:
            # Get proxy service from app state
            proxy_service = getattr(request.app.state, "proxy_service", None)
            if not proxy_service:
                # Fallback: try to create proxy service
                from ccproxy.config.settings import get_settings
                from ccproxy.core.http import BaseProxyClient, HTTPXClient
                from ccproxy.observability import get_metrics
                from ccproxy.services.credentials.manager import CredentialsManager
                from ccproxy.services.proxy_service import ProxyService

                settings = get_settings()
                http_client = HTTPXClient()
                proxy_client = BaseProxyClient(http_client)
                credentials_manager = CredentialsManager(config=settings.auth)
                metrics = get_metrics()

                proxy_service = ProxyService(
                    proxy_client=proxy_client,
                    credentials_manager=credentials_manager,
                    settings=settings,
                    proxy_mode="full",
                    target_base_url="https://api.anthropic.com",
                    metrics=metrics,
                    app_state=request.app.state,
                )

            self.proxy_service = proxy_service

            # Create OpenAI adapter for format conversion
            from ccproxy.adapters.openai.adapter import OpenAIAdapter

            self.openai_adapter = OpenAIAdapter()

            self._initialized = True
        except Exception as e:
            logger.error(f"Failed to initialize Claude API adapter: {e}")
            raise HTTPException(
                status_code=503, detail=f"Claude API initialization failed: {str(e)}"
            )

    async def handle_request(
        self, request: Request, endpoint: str, method: str, **kwargs: Any
    ) -> Response:
        """Handle a request to the Claude API using the proxy service.

        Args:
            request: FastAPI request object
            endpoint: Target endpoint path
            method: HTTP method
            **kwargs: Additional arguments

        Returns:
            Response from Claude API via proxy service
        """
        self._lazy_init(request)

        # Create provider context based on endpoint
        from ccproxy.services.provider_context import ProviderContext

        if endpoint == "/v1/messages":
            # Native Anthropic format - no conversion needed
            provider_context = ProviderContext(
                provider_name="claude-native",
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
                provider_name="claude-openai",
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
                detail=f"Endpoint {endpoint} not supported by Claude API plugin",
            )

        # Dispatch request through proxy service
        result = await self.proxy_service.dispatch_request(request, provider_context)

        # Handle different response types
        if isinstance(result, StreamingResponse):
            # For non-streaming requests from plugins, we need to consume the stream
            # and return a regular response
            import json

            # Check if the original request wanted streaming
            body = await request.body()
            try:
                request_data = json.loads(body) if body else {}
                if request_data.get("stream", False):
                    # Return the streaming response as-is
                    return result
            except json.JSONDecodeError:
                pass

            # Consume the streaming response and return as regular response
            # This is a simplified approach - in production, we'd properly handle this
            return result

        return result

    async def handle_streaming(
        self, request: Request, endpoint: str, **kwargs: Any
    ) -> StreamingResponse:
        """Handle a streaming request to the Claude API using the proxy service.

        Args:
            request: FastAPI request object
            endpoint: Target endpoint path
            **kwargs: Additional arguments

        Returns:
            Streaming response from Claude API via proxy service
        """
        self._lazy_init(request)

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

        # Create new request with updated body
        # Note: This is a simplified approach - in production we'd handle this more elegantly
        modified_request = Request(
            scope={
                **request.scope,
                "body": json.dumps(request_data).encode(),
            },
            receive=request.receive,
            send=request._send,
        )

        # Create provider context based on endpoint
        from ccproxy.services.provider_context import ProviderContext

        if endpoint == "/v1/messages":
            # Native Anthropic format
            provider_context = ProviderContext(
                provider_name="claude-native",
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
                provider_name="claude-openai",
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
                detail=f"Streaming not supported for endpoint {endpoint}",
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


class ClaudeAPIPlugin:
    """Claude API provider plugin.

    This plugin provides integration with the Anthropic Claude API,
    allowing direct HTTP access to Claude models through the proxy service.
    """

    @property
    def name(self) -> str:
        """Plugin name."""
        return "claude-api"

    @property
    def version(self) -> str:
        """Plugin version."""
        return "1.0.0"

    def create_adapter(self) -> BaseAdapter:
        """Create adapter instance."""
        return ClaudeAPIAdapter()

    def create_config(self) -> ProviderConfig:
        """Create provider configuration."""
        return ProviderConfig(
            name="claude-api",
            base_url="https://api.anthropic.com",
            supports_streaming=True,
            requires_auth=True,
            auth_type="x-api-key",
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

        The plugin itself is always ready - API keys are handled at runtime.
        """
        logger.info("Claude API plugin validation successful")
        return True
