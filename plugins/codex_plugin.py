"""OpenAI Codex provider plugin."""

from typing import Any

import structlog
from fastapi import HTTPException, Request
from starlette.responses import Response, StreamingResponse

from ccproxy.models.provider import ProviderConfig
from ccproxy.services.adapters.base import BaseAdapter


logger = structlog.get_logger(__name__)


class CodexPluginAdapter(BaseAdapter):
    """Codex adapter implementation.

    This plugin provides integration with OpenAI Codex through
    the existing Codex proxy service infrastructure.
    Handles both native Response API format and OpenAI Chat Completions format.
    """

    def __init__(self) -> None:
        """Initialize the Codex adapter."""
        self.proxy_service = None
        self.codex_adapter = None  # For format conversion
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
                    target_base_url=settings.codex.base_url,
                    metrics=metrics,
                    app_state=request.app.state,
                )

            self.proxy_service = proxy_service

            # Initialize Codex adapter for format conversion
            from ccproxy.adapters.openai.codex_adapter import CodexAdapter

            self.codex_adapter = CodexAdapter()

            self._initialized = True
        except Exception as e:
            logger.error(f"Failed to initialize Codex adapter: {e}")
            raise HTTPException(
                status_code=503, detail=f"Codex initialization failed: {str(e)}"
            )

    async def handle_request(
        self, request: Request, endpoint: str, method: str, **kwargs: Any
    ) -> Response:
        """Handle a request to the Codex API using the proxy service.

        Supports three endpoint patterns:
        1. /codex/responses - Native Response API with auto-generated session
        2. /codex/{session_id}/responses - Native Response API with specific session
        3. /codex/chat/completions - OpenAI Chat Completions format (converted)

        Args:
            request: FastAPI request object
            endpoint: Target endpoint path (e.g., "/codex/responses")
            method: HTTP method
            **kwargs: Additional arguments

        Returns:
            Response from Codex API via proxy service
        """
        self._lazy_init(request)

        # Create provider context for Codex
        import uuid

        from ccproxy.auth.openai import OpenAITokenManager

        # Get settings for Codex configuration
        from ccproxy.config.settings import get_settings
        from ccproxy.services.provider_context import ProviderContext

        settings = get_settings()

        # Use OpenAI token manager for Codex OAuth
        token_manager = OpenAITokenManager()

        # Parse endpoint to determine format and session handling
        session_id = None
        is_chat_format = False

        # Check endpoint pattern
        if endpoint == "/codex/chat/completions":
            # OpenAI Chat Completions format - needs conversion
            is_chat_format = True
            session_id = request.headers.get("session_id") or str(uuid.uuid4())

        elif endpoint == "/codex/responses":
            # Native Response API with auto-generated session
            session_id = request.headers.get("session_id") or str(uuid.uuid4())

        else:
            # Check for session_id in path (e.g., /codex/{session_id}/responses)
            path_parts = endpoint.split("/")
            if (
                len(path_parts) >= 4
                and path_parts[1] == "codex"
                and path_parts[3] == "responses"
            ):
                # Extract session_id from path
                session_id = path_parts[2]
            else:
                # Default: generate session ID
                session_id = str(uuid.uuid4())

        # Build provider context based on format
        if is_chat_format:
            # Chat Completions format - use Codex adapter for conversion
            provider_context = ProviderContext(
                provider_name="codex",
                auth_manager=token_manager,
                target_base_url=settings.codex.base_url,
                request_adapter=self.codex_adapter,  # Convert chat to response
                response_adapter=self.codex_adapter,  # Convert response to chat
                session_id=session_id,
                supports_streaming=True,
                requires_session=True,
                extra_headers={
                    "session_id": session_id,
                    "accept": "text/event-stream"
                    if request.headers.get("accept") == "text/event-stream"
                    else "application/json",
                },
            )
        else:
            # Native Response API format - no conversion needed
            provider_context = ProviderContext(
                provider_name="codex-native",
                auth_manager=token_manager,
                target_base_url=settings.codex.base_url,
                request_adapter=None,  # No conversion needed
                response_adapter=None,  # Pass through
                session_id=session_id,
                supports_streaming=True,
                requires_session=True,
                extra_headers={"session_id": session_id},
            )

        # Dispatch request through proxy service
        result = await self.proxy_service.dispatch_request(request, provider_context)

        return result

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
        # Codex streaming is handled through the main handle_request
        # since dispatch_request already handles streaming detection
        return await self.handle_request(request, endpoint, "POST", **kwargs)


class CodexPlugin:
    """OpenAI Codex provider plugin.

    This plugin provides integration with OpenAI Codex (ChatGPT backend),
    allowing ccproxy to forward requests to the Codex service with proper
    session management and authentication.
    """

    @property
    def name(self) -> str:
        """Plugin name."""
        return "codex"

    @property
    def version(self) -> str:
        """Plugin version."""
        return "1.0.0"

    def create_adapter(self) -> BaseAdapter:
        """Create adapter instance."""
        return CodexPluginAdapter()

    def create_config(self) -> ProviderConfig:
        """Create provider configuration."""
        return ProviderConfig(
            name="codex",
            base_url="https://chatgpt.com",
            supports_streaming=True,
            requires_auth=True,
            auth_type="oauth",
            requires_session=True,
            models=[
                "gpt-4",
                "gpt-4-turbo",
                "gpt-3.5-turbo",
                "o1-preview",
                "o1-mini",
            ],
        )

    async def validate(self) -> bool:
        """Validate plugin is ready.

        The plugin itself is always ready - Codex CLI and auth are handled at runtime.
        """
        # Check if Codex CLI is available (optional, for info only)
        try:
            import shutil

            codex_path = shutil.which("codex")
            if codex_path:
                logger.info(f"Codex CLI found at: {codex_path}")
            else:
                logger.info("Codex CLI not found, but plugin can still work with OAuth")
        except Exception:
            pass

        logger.info("Codex plugin validation successful")
        return True
