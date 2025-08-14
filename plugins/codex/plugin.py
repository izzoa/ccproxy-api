"""Codex provider plugin implementation."""

import uuid

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
        self._router_prefix = "/codex"
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
        if not self._config:
            return False
        # Basic validation - check if base_url is set
        return bool(self._config.base_url)

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
            provider_context = ProviderContext(
                provider_name="codex-native",
                auth_manager=OpenAITokenManager(),
                target_base_url=self._config.base_url if self._config else "https://chatgpt.com/backend-api/codex",
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
            provider_context = ProviderContext(
                provider_name="codex-native",
                auth_manager=OpenAITokenManager(),
                target_base_url=self._config.base_url if self._config else "https://chatgpt.com/backend-api/codex",
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
            """OpenAI-compatible chat completions endpoint for Codex."""
            from ccproxy.auth.openai import OpenAITokenManager
            from ccproxy.services.provider_context import ProviderContext

            # Get session_id from header if provided, otherwise generate
            header_session_id = request.headers.get("session_id")
            session_id = header_session_id or str(uuid.uuid4())

            # Get the core adapter for ProviderContext (which expects APIAdapter)
            core_adapter = self._adapter.get_core_adapter() if self._adapter else None

            # Build provider context with format conversion
            provider_context = ProviderContext(
                provider_name="codex",
                auth_manager=OpenAITokenManager(),
                target_base_url=self._config.base_url if self._config else "https://chatgpt.com/backend-api/codex",
                request_adapter=core_adapter,
                response_adapter=core_adapter,
                session_id=session_id,
                supports_streaming=True,
                requires_session=True,
                extra_headers={
                    "session_id": session_id,
                    "accept": "text/event-stream",
                },
            )

            # Dispatch to unified handler
            return await proxy_service.dispatch_request(request, provider_context)

        return router

    async def health_check(self) -> HealthCheckResult:
        """Perform health check for Codex plugin."""
        return await codex_health_check(self._config)
