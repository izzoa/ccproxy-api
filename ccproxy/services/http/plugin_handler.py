"""Refactored HTTP handler for plugin adapters using improved abstractions."""

from typing import Any, cast

import httpx
import structlog
from starlette.responses import Response, StreamingResponse

from ccproxy.core.errors import ProxyConnectionError, ProxyTimeoutError
from ccproxy.services.cache import ResponseCache
from ccproxy.services.handler_config import HandlerConfig
from ccproxy.services.http.base import BaseHTTPHandler
from ccproxy.services.http.processor import RequestProcessor


logger = structlog.get_logger(__name__)


class PluginHTTPHandler(BaseHTTPHandler):
    """Refactored HTTP handler for plugin adapters.

    Uses improved abstractions for request/response processing
    and better separation of concerns.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        logger: structlog.BoundLogger | None = None,
        response_cache: ResponseCache | None = None,
    ) -> None:
        """Initialize the HTTP handler.

        Args:
            http_client: Shared HTTP client instance
            logger: Optional structured logger instance
            response_cache: Optional response cache for performance optimization
        """
        self.logger = logger or structlog.get_logger(__name__)
        self._http_client = http_client
        self._processor = RequestProcessor(logger=self.logger)
        self._response_cache = response_cache

    async def handle_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
        handler_config: HandlerConfig,
        **kwargs: Any,
    ) -> Response | StreamingResponse:
        """Handle an HTTP request.

        Args:
            method: HTTP method
            url: Target URL
            headers: Request headers
            body: Request body
            handler_config: Handler configuration
            **kwargs: Additional handler-specific arguments

        Returns:
            Response or StreamingResponse
        """
        is_streaming = kwargs.get("is_streaming", False)
        streaming_handler = kwargs.get("streaming_handler")
        request_context = kwargs.get("request_context")

        if is_streaming and streaming_handler:
            # Delegate to streaming handler
            response: StreamingResponse = (
                await streaming_handler.handle_streaming_request(
                    method=method,
                    url=url,
                    headers=headers,
                    body=body,
                    handler_config=handler_config,
                    request_context=request_context or {},
                )
            )
            return response

        # Handle regular request
        return await self._handle_regular_request(
            method=method,
            url=url,
            headers=headers,
            body=body,
            handler_config=handler_config,
        )

    async def prepare_request(
        self,
        request_body: bytes,
        handler_config: HandlerConfig,
        auth_headers: dict[str, str] | None = None,
        request_headers: dict[str, str] | None = None,
        **extra_kwargs: Any,
    ) -> tuple[bytes, dict[str, str], bool]:
        """Prepare request for sending.

        Args:
            request_body: Original request body
            handler_config: Handler configuration
            auth_headers: Authentication headers to include
            request_headers: Original request headers
            **extra_kwargs: Additional plugin-specific parameters

        Returns:
            Tuple of (transformed_body, headers, is_streaming)
        """
        # Prepare base headers
        headers = dict(request_headers or {})
        if auth_headers:
            headers.update(auth_headers)

        # Process request through adapters and transformers
        (
            transformed_body,
            processed_headers,
            is_streaming,
        ) = await self._processor.process_request(
            body=request_body,
            headers=headers,
            handler_config=handler_config,
            **extra_kwargs,
        )

        return transformed_body, processed_headers, is_streaming

    async def _handle_regular_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
        handler_config: HandlerConfig,
    ) -> Response:
        """Handle a regular (non-streaming) HTTP request.

        Args:
            method: HTTP method
            url: Target URL
            headers: Request headers
            body: Request body
            handler_config: Handler configuration

        Returns:
            Response object
        """
        # Check cache for GET requests (if cache is available)
        if self._response_cache and method == "GET":
            cached_response = self._response_cache.get(method, url, body, headers)
            if cached_response and isinstance(cached_response, Response):
                self.logger.debug("cache_hit", url=url)
                return cast(Response, cached_response)

        try:
            # Make HTTP request
            response = await self._execute_request(method, url, headers, body)

            # Process response through adapters and transformers
            processed_body, processed_headers = await self._processor.process_response(
                body=response.content,
                headers=dict(response.headers),
                status_code=response.status_code,
                handler_config=handler_config,
            )

            result = Response(
                content=processed_body,
                status_code=response.status_code,
                headers=processed_headers,
            )

            # Cache successful GET responses
            if self._response_cache and method == "GET" and response.status_code == 200:
                self._response_cache.set(method, url, result, body, headers)

            return result

        except httpx.TimeoutException as e:
            self.logger.error("http_request_timeout", url=url, error=str(e), exc_info=e)
            raise ProxyTimeoutError(f"Request timed out: {url}") from e
        except httpx.ConnectError as e:
            self.logger.error("http_connect_error", url=url, error=str(e), exc_info=e)
            raise ProxyConnectionError(f"Connection failed: {url}") from e
        except httpx.HTTPError as e:
            self.logger.error("http_error", url=url, error=str(e), exc_info=e)
            raise ProxyConnectionError(f"HTTP error: {e}") from e
        except Exception as e:
            self.logger.error(
                "http_request_unexpected_error", url=url, error=str(e), exc_info=e
            )
            raise ProxyConnectionError(f"Unexpected error: {e}") from e

    async def _execute_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> httpx.Response:
        """Execute the actual HTTP request.

        Args:
            method: HTTP method
            url: Target URL
            headers: Request headers
            body: Request body

        Returns:
            HTTPX Response object

        Raises:
            RuntimeError: If no HTTP client is available
        """
        if not self._http_client:
            raise RuntimeError(
                "No HTTP client available - must provide http_client in constructor"
            )

        # Use shared HTTP client
        return await self._http_client.request(
            method=method,
            url=url,
            headers=headers,
            content=body,
            timeout=httpx.Timeout(120.0),
        )

    async def cleanup(self) -> None:
        """Cleanup HTTP handler resources.

        Note: This handler uses shared HTTP clients that are managed
        by the ServiceContainer. We don't close them here to avoid conflicts.
        Only clears references to prevent memory leaks.
        """
        try:
            # Clear references but don't close shared client
            self._http_client = None
            self.logger.debug("plugin_http_handler_cleanup_completed")

        except Exception as e:
            self.logger.error(
                "plugin_http_handler_cleanup_failed",
                error=str(e),
                exc_info=e,
            )
