"""Codex provider plugin implementation."""

import uuid
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from ccproxy.api.dependencies import ProxyServiceDep
from ccproxy.auth.conditional import ConditionalAuthDep
from ccproxy.core.services import CoreServices
from ccproxy.models.provider import ProviderConfig
from ccproxy.plugins.protocol import HealthCheckResult, ProviderPlugin
from ccproxy.services.adapters.base import BaseAdapter

from .adapter import CodexAdapter
from .config import CodexSettings
from .health import codex_health_check


class Plugin(ProviderPlugin):
    """Codex provider plugin."""

    def __init__(self) -> None:
        self._name = "codex"
        self._version = "1.0.0"
        self._router_prefix = "/api/codex"
        self._adapter: CodexAdapter | None = None
        self._config: CodexSettings | None = None
        self._services: CoreServices | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def version(self) -> str:
        return self._version

    @property
    def router_prefix(self) -> str:
        return self._router_prefix

    async def initialize(self, services: CoreServices) -> None:
        """Initialize plugin with shared services."""
        self._services = services

        # Load plugin-specific configuration
        # Check for legacy codex config first
        if hasattr(services.settings, "codex"):
            # Use legacy config structure
            legacy_config = services.settings.codex
            plugin_config = {
                "name": self.name,
                "base_url": legacy_config.base_url,
                "supports_streaming": True,
                "requires_auth": True,
                "auth_type": "oauth",
                "models": ["gpt-4", "gpt-4-turbo"],
                "oauth": legacy_config.oauth.model_dump(),
                "callback_port": legacy_config.callback_port,
                "redirect_uri": legacy_config.redirect_uri,
                "verbose_logging": legacy_config.verbose_logging,
            }
        else:
            # Use plugin config from settings
            plugin_config = getattr(services.settings, "plugins", {}).get(self.name, {})

        self._config = CodexSettings.model_validate(plugin_config)

        # Initialize adapter with shared HTTP client
        self._adapter = CodexAdapter(
            http_client=services.http_client,
            logger=services.logger.bind(plugin=self.name),
        )

        # Set up authentication manager for the adapter
        from ccproxy.auth.openai import OpenAITokenManager

        auth_manager = OpenAITokenManager()
        services.logger.info(
            "codex_plugin_auth_setup",
            auth_manager_type=type(auth_manager).__name__,
            storage_location=auth_manager.get_storage_location(),
        )

        # Check if we have valid credentials
        has_creds = await auth_manager.has_credentials()
        if has_creds:
            token = await auth_manager.get_valid_token()
            services.logger.info(
                "codex_plugin_auth_status",
                has_credentials=True,
                has_valid_token=bool(token),
                token_preview=token[:20] + "..." if token else None,
            )
        else:
            services.logger.warning(
                "codex_plugin_no_auth",
                msg="No OpenAI credentials found. Run 'ccproxy auth login --provider openai' to authenticate.",
            )

        self._adapter.set_auth_manager(auth_manager)
        services.logger.info("codex_plugin_auth_manager_set", adapter_has_auth=True)

        # Set up detection service for the adapter
        from ccproxy.services.codex_detection_service import CodexDetectionService

        detection_service = CodexDetectionService(services.settings)
        self._adapter.set_detection_service(detection_service)
        services.logger.info(
            "codex_plugin_detection_service_set", adapter_has_detection=True
        )

    async def shutdown(self) -> None:
        """Cleanup on shutdown."""
        if self._adapter:
            await self._adapter.cleanup()

    def create_adapter(self) -> BaseAdapter:
        if not self._adapter:
            raise RuntimeError("Plugin not initialized")
        return self._adapter

    def create_config(self) -> ProviderConfig:
        if not self._config:
            raise RuntimeError("Plugin not initialized")
        return self._config

    async def validate(self) -> bool:
        """Check if Codex configuration is valid."""
        # Always return True - actual validation happens during initialization
        # The plugin system calls validate() before initialize(), so we can't
        # check config here since it hasn't been loaded yet
        return True

    def get_routes(self) -> APIRouter | None:
        """Return Codex-specific routes."""
        router = APIRouter(tags=[f"plugin-{self.name}"])

        @router.post("/responses", response_model=None)
        async def codex_responses(
            request: Request,
            proxy_service: ProxyServiceDep,
            auth: ConditionalAuthDep,
        ) -> StreamingResponse | Response:
            """Create Codex completion with auto-generated session_id."""
            from ccproxy.auth.openai import OpenAITokenManager
            from ccproxy.services.provider_context import ProviderContext

            # Get session_id from header if provided
            header_session_id = request.headers.get("session_id")
            session_id = header_session_id or str(uuid.uuid4())

            # Use plugin dispatch through the codex plugin
            base_url = self._config.base_url if self._config else "https://chatgpt.com"
            provider_context = ProviderContext(
                provider_name="codex-native",
                auth_manager=OpenAITokenManager(),
                target_base_url=f"{base_url}/backend-api/codex",
                request_adapter=None,  # No conversion needed for native API
                response_adapter=None,  # Pass through
                session_id=session_id,
                supports_streaming=True,
                requires_session=True,
                extra_headers={"session_id": session_id},
            )

            # Dispatch to unified handler
            return await proxy_service.dispatch_request(request, provider_context)

        @router.post("/{session_id}/responses", response_model=None)
        async def codex_responses_with_session(
            session_id: str,
            request: Request,
            proxy_service: ProxyServiceDep,
            auth: ConditionalAuthDep,
        ) -> StreamingResponse | Response:
            """Create Codex completion with specific session_id."""
            from ccproxy.auth.openai import OpenAITokenManager
            from ccproxy.services.provider_context import ProviderContext

            # Build provider context with path-provided session_id
            base_url = self._config.base_url if self._config else "https://chatgpt.com"
            provider_context = ProviderContext(
                provider_name="codex-native",
                auth_manager=OpenAITokenManager(),
                target_base_url=f"{base_url}/backend-api/codex",
                request_adapter=None,  # No conversion needed for native API
                response_adapter=None,  # Pass through
                session_id=session_id,
                supports_streaming=True,
                requires_session=True,
                extra_headers={"session_id": session_id},
            )

            # Dispatch to unified handler
            return await proxy_service.dispatch_request(request, provider_context)

        @router.post("/chat/completions", response_model=None)
        async def codex_chat_completions(
            request: Request,
            proxy_service: ProxyServiceDep,
            auth: ConditionalAuthDep,
        ) -> StreamingResponse | Response:
            """OpenAI-compatible chat completions endpoint for Codex.

            This endpoint accepts OpenAI-format chat completions requests and
            converts them to Codex Response API format before forwarding.
            """
            import json

            from starlette.datastructures import Headers
            from starlette.requests import Request as StarletteRequest

            # Get session_id from header if provided, otherwise generate
            header_session_id = request.headers.get("session_id")
            session_id = header_session_id or str(uuid.uuid4())

            # Read the body to check if streaming is requested
            body = await request.body()
            is_streaming = False
            if body:
                request_data = json.loads(body)
                is_streaming = request_data.get("stream", False)

            # Create a new request with the body we already read
            # This is necessary because we consumed the body above
            async def receive() -> dict[str, Any]:
                return {"type": "http.request", "body": body}

            new_request = StarletteRequest(
                scope={
                    **request.scope,
                    "type": "http",
                },
                receive=receive,
            )
            # Copy headers
            new_request._headers = Headers(raw=request.headers.raw)

            # Use the adapter directly to handle the request with format conversion
            # The adapter will convert OpenAI format to Codex format and forward to
            # the correct URL: https://chatgpt.com/backend-api/codex/responses
            if not self._adapter:
                return Response(
                    content=json.dumps({"error": "Codex adapter not initialized"}),
                    status_code=500,
                    media_type="application/json",
                )

            if is_streaming:
                return await self._adapter.handle_streaming(
                    new_request, "/responses", session_id=session_id
                )
            else:
                return await self._adapter.handle_request(
                    new_request, "/responses", "POST", session_id=session_id
                )

        return router

    async def health_check(self) -> HealthCheckResult:
        """Perform health check for Codex plugin."""
        return await codex_health_check(self._config)
