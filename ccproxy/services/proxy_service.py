"""Refactored ProxyService - orchestrates proxy requests using injected services."""

import json
import uuid
from typing import Any

import httpx
import structlog
from fastapi import HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from ccproxy.config.settings import Settings
from ccproxy.core.http import BaseProxyClient
from ccproxy.observability.metrics import PrometheusMetrics
from ccproxy.services.auth import AuthenticationService
from ccproxy.services.config import ProxyConfiguration
from ccproxy.services.credentials.manager import CredentialsManager
from ccproxy.services.mocking import MockResponseHandler
from ccproxy.services.plugins import PluginManager
from ccproxy.services.provider_context import ProviderContext
from ccproxy.services.request_context import ProxyRequestContext, create_proxy_context
from ccproxy.services.streaming import StreamingHandler
from ccproxy.services.tracing import CoreRequestTracer
from ccproxy.services.transformation import RequestTransformer


logger = structlog.get_logger(__name__)


class ProxyService:
    """Orchestrates proxy requests using injected services."""

    def __init__(
        self,
        # Core dependencies
        proxy_client: BaseProxyClient,
        credentials_manager: CredentialsManager,
        settings: Settings,
        # Injected services
        request_tracer: CoreRequestTracer,
        mock_handler: MockResponseHandler,
        streaming_handler: StreamingHandler,
        request_transformer: RequestTransformer,
        auth_service: AuthenticationService,
        config: ProxyConfiguration,
        plugin_manager: PluginManager,
        metrics: PrometheusMetrics | None = None,
    ) -> None:
        """Initialize with all dependencies injected.

        - No service creation inside __init__
        - All dependencies passed from container
        - Stores references only
        """
        # Core dependencies
        self.proxy_client = proxy_client
        self.credentials_manager = credentials_manager
        self.settings = settings

        # Injected services
        self.request_tracer = request_tracer
        self.mock_handler = mock_handler
        self.streaming_handler = streaming_handler
        self.request_transformer = request_transformer
        self.auth_service = auth_service
        self.config = config
        self.plugin_manager = plugin_manager
        self.metrics = metrics

        # HTTP client for regular requests
        self._http_client: httpx.AsyncClient | None = None

        logger.info("ProxyService initialized with injected services")

    async def dispatch_request(
        self, request: Request, provider_context: ProviderContext
    ) -> Response | StreamingResponse:
        """Main entry point - pure delegation pattern.

        Flow:
        1. Prepare request context
        2. Check for plugin adapter
        3. If plugin exists, delegate to adapter
        4. Otherwise, handle via standard proxy flow
        """
        try:
            # 1. Prepare request context
            request_id = str(uuid.uuid4())
            ctx = create_proxy_context(
                request_id=request_id,
                provider=provider_context.provider_name,
                endpoint=str(request.url.path),
                method=request.method,
            )

            # Read request body
            body = await request.body()

            # 2. Check for plugin adapter
            adapter = self.plugin_manager.get_plugin_adapter(
                provider_context.provider_name
            )

            if adapter:
                # 3. Pure delegation to plugin adapter
                logger.info(
                    f"Delegating to plugin adapter: {provider_context.provider_name}"
                )

                # Trace request
                plugin_tracer = self.plugin_manager.get_plugin_tracer(
                    provider_context.provider_name
                )
                tracer = plugin_tracer or self.request_tracer

                await tracer.trace_request(
                    request_id,
                    request.method,
                    str(request.url),
                    dict(request.headers),
                    body,
                )

                # Delegate to adapter
                response = await adapter.handle_request(
                    request,
                    str(request.url.path),
                    request.method,
                )

                # Trace response (if not streaming)
                if not isinstance(response, StreamingResponse):
                    response_body = b""
                    if hasattr(response, "body"):
                        if isinstance(response.body, memoryview):
                            response_body = bytes(response.body)
                        else:
                            response_body = response.body
                    await tracer.trace_response(
                        request_id,
                        response.status_code,
                        dict(response.headers),
                        response_body,
                    )

                # Update metrics
                if self.metrics:
                    # Extract model from body if possible
                    model = None
                    try:
                        body_json = json.loads(body) if body else {}
                        model = body_json.get("model")
                    except Exception:
                        pass

                    self.metrics.record_request(
                        method=ctx.method or "unknown",
                        endpoint=ctx.endpoint or "unknown",
                        model=model,
                        status=response.status_code,
                        service_type=provider_context.provider_name,
                    )

                return response

            else:
                # 4. Standard proxy flow (non-plugin providers)
                return await self._handle_standard_proxy_request(
                    request, body, provider_context, ctx, request_id
                )

        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                "Request dispatch failed",
                error=str(e),
                provider=provider_context.provider_name,
            )
            raise HTTPException(status_code=500, detail=f"Proxy error: {str(e)}") from e

    async def _handle_standard_proxy_request(
        self,
        request: Request,
        body: bytes,
        provider_context: ProviderContext,
        ctx: ProxyRequestContext,
        request_id: str,
    ) -> Response | StreamingResponse:
        """Handle standard proxy flow for non-plugin providers.

        This preserves the original proxy logic for providers that don't have adapters.
        """
        # Get authentication headers if needed
        auth_headers = {}
        if provider_context.auth_manager:
            auth_headers = await provider_context.auth_manager.get_auth_headers()
        elif getattr(provider_context, "requires_auth", False):
            # Get OAuth token for providers that need it
            token = await self.auth_service.get_access_token()
            auth_headers = {"Authorization": f"Bearer {token}"}

        # Transform request
        # Extract metadata
        model, is_streaming = self.request_transformer.extract_request_metadata(body)
        ctx.model = model

        # Transform body
        transformed_body = await self.request_transformer.transform_body(
            body, provider_context.request_adapter, provider_context
        )

        # Build target URL
        target_url = self.request_transformer.build_target_url(
            provider_context.target_base_url,
            str(request.url.path),
            str(request.url.query) if request.url.query else None,
            provider_context,
        )

        # Prepare headers
        request_headers = dict(request.headers)
        headers = await self.request_transformer.prepare_headers(
            request_headers,
            auth_headers,
            {},  # No extra headers at this level
            provider_context,
        )

        # Determine if streaming needed
        if not is_streaming and provider_context.supports_streaming:
            is_streaming = await self.streaming_handler.should_stream(
                transformed_body, provider_context
            )

        # Trace request
        await self.request_tracer.trace_request(
            request_id, request.method, target_url, headers, transformed_body
        )

        # Route to appropriate handler
        response = await self._route_request(
            request.method,
            target_url,
            headers,
            transformed_body,
            provider_context,
            is_streaming,
            ctx,
        )

        # Trace response (if not streaming)
        if not isinstance(response, StreamingResponse):
            response_body = b""
            if hasattr(response, "body"):
                if isinstance(response.body, memoryview):
                    response_body = bytes(response.body)
                else:
                    response_body = response.body
            await self.request_tracer.trace_response(
                request_id,
                response.status_code,
                dict(response.headers),
                response_body,
            )

        # Update metrics
        if self.metrics:
            self.metrics.record_request(
                method=ctx.method or "unknown",
                endpoint=ctx.endpoint or "unknown",
                model=ctx.model,
                status=response.status_code,
                service_type=provider_context.provider_name,
            )

        return response

    async def _route_request(
        self,
        method: str,
        target_url: str,
        headers: dict[str, str],
        body: bytes,
        provider_context: ProviderContext,
        is_streaming: bool,
        request_context: ProxyRequestContext,
    ) -> Response | StreamingResponse:
        """Route to appropriate handler based on mode.

        Routing priority:
        1. Bypass mode → MockHandler
        2. Streaming → StreamingHandler
        3. Regular → _handle_regular_request

        Note: Plugin handling is now done in dispatch_request via pure delegation.
        """
        # 1. Check bypass mode
        if getattr(self.settings.server, "bypass_mode", False):
            logger.info("Bypass mode: generating mock response")

            message_type = self.mock_handler.extract_message_type(body)
            is_openai = provider_context.provider_name == "openai"

            if is_streaming:
                return await self.mock_handler.generate_streaming_response(
                    request_context.model or "unknown",
                    is_openai,
                    request_context.base_context,
                    message_type,
                )
            else:
                (
                    status,
                    mock_headers,
                    mock_body,
                ) = await self.mock_handler.generate_standard_response(
                    request_context.model or "unknown",
                    is_openai,
                    request_context.base_context,
                    message_type,
                )
                return Response(
                    content=mock_body, status_code=status, headers=mock_headers
                )

        # 2. Check for streaming
        if is_streaming:
            logger.info("Handling streaming request")
            return await self.streaming_handler.handle_streaming_request(
                method,
                target_url,
                headers,
                body,
                provider_context,
                request_context.base_context,
                self.config.get_httpx_client_config(),
            )

        # 3. Regular HTTP request
        return await self._handle_regular_request(
            method, target_url, headers, body, provider_context, request_context
        )

    async def _handle_regular_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
        provider_context: ProviderContext,
        request_context: ProxyRequestContext,
    ) -> Response:
        """Execute standard HTTP request.

        - Uses httpx.AsyncClient
        - Applies response adapter if provided
        - Applies response transformer if provided
        - Returns FastAPI Response
        """
        try:
            # Get HTTP client config
            client_config = self.config.get_httpx_client_config()

            # Make the request
            async with httpx.AsyncClient(**client_config) as client:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    content=body,
                    timeout=httpx.Timeout(120.0),
                )

                # Read response body
                response_body = response.content

                # Format error responses if needed
                if response.status_code >= 400:
                    response_body = self._format_error_response(
                        response.status_code, response_body, provider_context
                    )

                # Apply response adapter if provided
                if provider_context.response_adapter and response.status_code < 400:
                    try:
                        response_data = json.loads(response_body)
                        adapted_data = (
                            await provider_context.response_adapter.adapt_response(
                                response_data
                            )
                        )
                        response_body = json.dumps(adapted_data).encode()
                    except Exception as e:
                        logger.warning("Failed to adapt response", error=str(e))

                # Apply response transformer if provided
                if provider_context.response_transformer:
                    if hasattr(
                        provider_context.response_transformer, "transform_headers"
                    ):
                        # It's a transformer object with methods
                        transformed_headers = (
                            provider_context.response_transformer.transform_headers(
                                dict(response.headers)
                            )
                        )
                    else:
                        # It's a callable - cast to indicate it's the correct type
                        from collections.abc import Callable
                        from typing import cast

                        transformer_func = cast(
                            Callable[[dict[str, str]], dict[str, str]],
                            provider_context.response_transformer,
                        )
                        transformed_headers = transformer_func(dict(response.headers))

                    if transformed_headers:
                        response_headers = transformed_headers
                    else:
                        response_headers = dict(response.headers)
                else:
                    response_headers = dict(response.headers)

                # Update metrics
                if request_context:
                    request_context.metrics["response_size"] = len(response_body)
                    request_context.metrics["status_code"] = response.status_code

                return Response(
                    content=response_body,
                    status_code=response.status_code,
                    headers=response_headers,
                )

        except httpx.TimeoutException as e:
            logger.error("Request timeout", url=url)
            raise HTTPException(status_code=504, detail="Request timeout") from e
        except Exception as e:
            logger.error("Request failed", url=url, error=str(e))
            raise HTTPException(
                status_code=502, detail=f"Upstream error: {str(e)}"
            ) from e

    def _format_error_response(
        self, status_code: int, body: bytes, provider_context: ProviderContext
    ) -> bytes:
        """Format error response based on provider.

        - Attempts JSON parsing
        - Uses adapter.format_error if available
        - Returns formatted error bytes
        """
        try:
            # Try to parse as JSON
            error_data = json.loads(body)

            # Use adapter to format if available
            if provider_context.response_adapter and hasattr(
                provider_context.response_adapter, "format_error"
            ):
                formatted = provider_context.response_adapter.format_error(
                    error_data, status_code
                )
                return json.dumps(formatted).encode()

            return body

        except Exception:
            # Return original body if parsing/formatting fails
            return body

    async def initialize_plugins(self, scheduler: Any | None = None) -> None:
        """Initialize plugin system at startup.

        - Delegates to plugin_manager
        - Called once during app startup
        """
        # Create HTTP client for plugins
        client_config = self.config.get_httpx_client_config()
        http_client = httpx.AsyncClient(**client_config)

        # Initialize plugins with proper parameters
        await self.plugin_manager.initialize_plugins(http_client, self, scheduler)

        # Store HTTP client reference
        self._http_client = http_client

    async def close(self) -> None:
        """Clean up resources on shutdown.

        - Closes proxy client
        - Closes credentials manager
        - Any other cleanup needed
        """
        try:
            # Close HTTP client if exists
            if self._http_client:
                await self._http_client.aclose()

            # Close plugin manager
            await self.plugin_manager.close()

            # Close proxy client
            if hasattr(self.proxy_client, "close"):
                await self.proxy_client.close()

            # Close credentials manager
            if hasattr(self.credentials_manager, "close"):
                await self.credentials_manager.close()

            logger.info("ProxyService cleanup complete")

        except Exception as e:
            logger.error("Error during cleanup", error=str(e))
