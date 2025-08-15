"""Codex provider plugin implementation."""

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from ccproxy.api.dependencies import ProxyServiceDep
from ccproxy.auth.conditional import ConditionalAuthDep
from ccproxy.core.services import CoreServices
from ccproxy.models.provider import ProviderConfig
from ccproxy.plugins.protocol import (
    HealthCheckResult,
    ProviderPlugin,
    ScheduledTaskDefinition,
)
from ccproxy.services.adapters.base import BaseAdapter

from .adapter import CodexAdapter
from .config import CodexSettings
from .health import codex_health_check
from .tasks import CodexDetectionRefreshTask


logger = structlog.get_logger(__name__)


class Plugin(ProviderPlugin):
    """Codex provider plugin."""

    def __init__(self) -> None:
        self._name = "codex"
        self._version = "1.0.0"
        self._router_prefix = "/api/codex"
        self._adapter: CodexAdapter | None = None
        self._config: CodexSettings | None = None
        self._services: CoreServices | None = None
        self._detection_service: Any | None = None  # Will be CodexDetectionService
        self._auth_manager: Any | None = None  # Will be OpenAITokenManager

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

        # Load plugin-specific configuration from plugins dictionary
        plugin_config = getattr(services.settings, "plugins", {}).get(self.name, {})

        # If no config provided, use defaults from CodexSettings
        if not plugin_config:
            plugin_config = {
                "name": self.name,
                "base_url": "https://chatgpt.com/backend-api/codex",
                "supports_streaming": True,
                "requires_auth": True,
                "auth_type": "oauth",
                "models": ["gpt-5"],
            }

        self._config = CodexSettings.model_validate(plugin_config)

        # Initialize adapter with shared HTTP client
        self._adapter = CodexAdapter(
            http_client=services.http_client,
            logger=logger.bind(plugin=self.name),
        )

        # Set up authentication manager for the adapter
        from ccproxy.auth.openai import OpenAITokenManager

        auth_manager = OpenAITokenManager()
        logger.info(
            "codex_plugin_auth_setup",
            auth_manager_type=type(auth_manager).__name__,
            storage_location=auth_manager.get_storage_location(),
        )

        # Check if we have valid credentials
        has_creds = await auth_manager.has_credentials()
        if has_creds:
            token = await auth_manager.get_valid_token()
            logger.info(
                "codex_plugin_auth_status",
                has_credentials=True,
                has_valid_token=bool(token),
                token_preview=token[:20] + "..." if token else None,
            )
        else:
            logger.warning(
                "codex_plugin_no_auth",
                msg="No OpenAI credentials found. Run 'ccproxy auth login --provider openai' to authenticate.",
            )

        self._adapter.set_auth_manager(auth_manager)
        self._auth_manager = auth_manager  # Store for health checks
        logger.info("codex_plugin_auth_manager_set", adapter_has_auth=True)

        # Set up detection service for the adapter
        from .detection_service import CodexDetectionService

        detection_service = CodexDetectionService(services.settings)

        # Initialize detection service to capture Codex CLI headers
        logger.info("codex_plugin_initializing_detection")
        try:
            await detection_service.initialize_detection()

            # Log Codex CLI status
            version = detection_service.get_version()
            binary_path = detection_service.get_binary_path()

            if binary_path:
                logger.info(
                    "codex_cli_available",
                    status="available",
                    version=version,
                    binary_path=binary_path,
                )
            else:
                logger.warning(
                    "codex_cli_not_found",
                    status="not_found",
                    msg="Codex CLI not found in PATH or common locations",
                )

            logger.info(
                "codex_plugin_detection_initialized",
                has_cached_data=detection_service.get_cached_data() is not None,
                version=version,
            )
        except Exception as e:
            logger.warning(
                "codex_plugin_detection_initialization_failed",
                error=str(e),
                msg="Using fallback Codex instructions",
            )

        self._adapter.set_detection_service(detection_service)
        self._detection_service = detection_service  # Store for health checks
        logger.info("codex_plugin_detection_service_set", adapter_has_detection=True)

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
        # The detection service will handle CLI availability checking
        return True

    def get_routes(self) -> APIRouter | None:
        """Return Codex-specific routes."""
        router = APIRouter(tags=[f"plugin-{self.name}"])

        # Define path transformer for Codex routes
        def codex_path_transformer(path: str) -> str:
            """Transform stripped paths to Codex API paths."""
            # Simple transformation - just return the stripped path
            # The base_url already contains /backend-api/codex
            if path == "/chat/completions":
                return "/responses"  # Map OpenAI endpoint to Codex endpoint
            # For everything else, just return as-is
            return path

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
            base_url = (
                self._config.base_url
                if self._config
                else "https://chatgpt.com/backend-api/codex"
            )

            provider_context = ProviderContext(
                provider_name="codex-native",
                auth_manager=OpenAITokenManager(),
                target_base_url=base_url,  # This already includes /backend-api/codex
                route_prefix=self._router_prefix,  # Use plugin's prefix
                path_transformer=codex_path_transformer,  # Use our transformer
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
            base_url = (
                self._config.base_url
                if self._config
                else "https://chatgpt.com/backend-api/codex"
            )
            provider_context = ProviderContext(
                provider_name="codex-native",
                auth_manager=OpenAITokenManager(),
                target_base_url=base_url,  # This already includes /backend-api/codex
                route_prefix=self._router_prefix,  # Use plugin's prefix
                path_transformer=codex_path_transformer,  # Use our transformer
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
        return await codex_health_check(
            self._config, self._detection_service, self._auth_manager
        )

    def get_scheduled_tasks(self) -> list[ScheduledTaskDefinition] | None:
        """Get scheduled task definitions for Codex plugin.

        Returns:
            List with detection refresh task or None if detection service not available
        """
        if not self._detection_service:
            return None

        # Create the task definition
        task_def: ScheduledTaskDefinition = {
            "task_name": f"codex_detection_refresh_{self.name}",
            "task_type": "codex_detection_refresh",
            "task_class": CodexDetectionRefreshTask,
            "interval_seconds": 3600.0,  # Refresh every hour
            "enabled": True,
            # detection_service will be passed through task initialization
        }

        return [task_def]
