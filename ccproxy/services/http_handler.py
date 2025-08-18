"""HTTP request handler for plugin adapters.

This module provides a shared HTTP handler that plugin adapters can use
to make direct HTTP requests without calling back to ProxyService.
"""

import json
from typing import Any

import httpx
import structlog
from fastapi import HTTPException
from starlette.responses import Response, StreamingResponse

from ccproxy.core.errors import ProxyConnectionError, ProxyTimeoutError
from ccproxy.services.handler_config import HandlerConfig


logger = structlog.get_logger(__name__)


class PluginHTTPHandler:
    """Handles HTTP requests for plugin adapters.

    This handler is used by plugin adapters to make direct HTTP requests
    to their target APIs, avoiding circular dependencies with ProxyService.
    """

    def __init__(self, client_config: dict[str, Any] | None = None):
        """Initialize the HTTP handler.

        Args:
            client_config: Optional HTTPX client configuration
        """
        self.client_config = client_config or {}
        self.logger = logger

    async def handle_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
        handler_config: HandlerConfig,
        is_streaming: bool = False,
        streaming_handler: Any | None = None,
        request_context: dict[str, Any] | None = None,
    ) -> Response | StreamingResponse:
        """Handle an HTTP request.

        Args:
            method: HTTP method
            url: Target URL
            headers: Request headers
            body: Request body
            handler_config: Provider context with adapters and transformers
            is_streaming: Whether this is a streaming request
            streaming_handler: Optional streaming handler for SSE requests
            request_context: Optional request context for observability

        Returns:
            Response or StreamingResponse
        """
        if is_streaming and streaming_handler:
            # Delegate to streaming handler
            response: (
                Response | StreamingResponse
            ) = await streaming_handler.handle_streaming_request(
                method=method,
                url=url,
                headers=headers,
                body=body,
                handler_config=handler_config,
                request_context=request_context or {},
                client_config=self.client_config,
            )
            return response
        else:
            # Make regular HTTP request
            return await self._handle_regular_request(
                method=method,
                url=url,
                headers=headers,
                body=body,
                handler_config=handler_config,
            )

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
            handler_config: Provider context with adapters and transformers

        Returns:
            Response object
        """
        try:
            async with httpx.AsyncClient(**self.client_config) as client:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    content=body,
                    timeout=httpx.Timeout(120.0),
                )

                # Read response body
                response_body = response.content
                response_headers = dict(response.headers)

                # Apply response adapter if needed
                if handler_config.response_adapter and response.status_code < 400:
                    try:
                        response_data = json.loads(response_body)
                        adapted_data = (
                            await handler_config.response_adapter.adapt_response(
                                response_data
                            )
                        )
                        response_body = json.dumps(adapted_data).encode()
                    except json.JSONDecodeError as e:
                        self.logger.warning(
                            "response_adaptation_json_decode_error",
                            error=str(e),
                            exc_info=e,
                        )
                    except UnicodeDecodeError as e:
                        self.logger.warning(
                            "response_adaptation_unicode_decode_error",
                            error=str(e),
                            exc_info=e,
                        )
                    except Exception as e:
                        self.logger.warning(
                            "response_adaptation_unexpected_error",
                            error=str(e),
                            exc_info=e,
                        )

                # Apply response transformer if provided
                if handler_config.response_transformer:
                    if hasattr(
                        handler_config.response_transformer, "transform_headers"
                    ):
                        # It's a transformer object with methods
                        transformed_headers = (
                            handler_config.response_transformer.transform_headers(
                                response_headers
                            )
                        )
                        if transformed_headers:
                            response_headers = transformed_headers
                    elif callable(handler_config.response_transformer):
                        # It's a callable function
                        try:
                            transformed_headers = handler_config.response_transformer(
                                response_headers
                            )
                            if transformed_headers:
                                response_headers = transformed_headers
                        except TypeError as e:
                            self.logger.warning(
                                "response_header_transform_type_error",
                                error=str(e),
                                exc_info=e,
                            )
                        except Exception as e:
                            self.logger.warning(
                                "response_header_transform_unexpected_error",
                                error=str(e),
                                exc_info=e,
                            )

                return Response(
                    content=response_body,
                    status_code=response.status_code,
                    headers=response_headers,
                )

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

    async def prepare_request(
        self,
        request_body: bytes,
        handler_config: HandlerConfig,
        auth_headers: dict[str, str] | None = None,
        request_headers: dict[str, str] | None = None,
        session_id: str | None = None,
        access_token: str | None = None,
        **extra_kwargs: Any,
    ) -> tuple[bytes, dict[str, str], bool]:
        """Prepare request for sending.

        Applies format adapters and transformers to prepare the request.

        Args:
            request_body: Original request body
            handler_config: Provider context with adapters and transformers
            auth_headers: Authentication headers to include
            request_headers: Original request headers
            session_id: Optional session ID for stateful requests
            access_token: Optional access token for authentication
            **extra_kwargs: Additional plugin-specific parameters

        Returns:
            Tuple of (transformed_body, headers, is_streaming)
        """
        # Parse request body if needed
        request_data = {}
        is_streaming = False

        if request_body:
            try:
                request_data = json.loads(request_body)
                is_streaming = request_data.get("stream", False)
            except json.JSONDecodeError:
                pass

        # Apply request adapter if provided
        transformed_body = request_body
        if handler_config.request_adapter and request_body:
            try:
                adapted_data = await handler_config.request_adapter.adapt_request(
                    request_data
                )
                transformed_body = json.dumps(adapted_data).encode()
            except json.JSONDecodeError as e:
                self.logger.warning(
                    "request_adaptation_json_decode_error",
                    error=str(e),
                    exc_info=e,
                )
            except UnicodeDecodeError as e:
                self.logger.warning(
                    "request_adaptation_unicode_decode_error",
                    error=str(e),
                    exc_info=e,
                )
            except Exception as e:
                self.logger.warning(
                    "request_adaptation_unexpected_error",
                    error=str(e),
                    exc_info=e,
                )

        # Prepare headers
        headers = dict(request_headers or {})
        if auth_headers:
            headers.update(auth_headers)

        # Apply request transformer if provided
        if handler_config.request_transformer and hasattr(
            handler_config.request_transformer, "transform_headers"
        ):
            # It's a transformer object with methods
            kwargs = {}
            if session_id:
                kwargs["session_id"] = session_id
            if access_token:
                kwargs["access_token"] = access_token
            kwargs.update(extra_kwargs)

            try:
                transformed_headers = (
                    handler_config.request_transformer.transform_headers(
                        headers, **kwargs
                    )
                )
                if transformed_headers:
                    headers = transformed_headers
            except TypeError as e:
                # If the transformer doesn't accept the kwargs, try without them
                self.logger.debug(
                    "Transformer doesn't accept kwargs, trying without them",
                    error=str(e),
                )
                transformed_headers = (
                    handler_config.request_transformer.transform_headers(headers)
                )
                if transformed_headers:
                    headers = transformed_headers

            # Transform body if the transformer has that method
            if hasattr(handler_config.request_transformer, "transform_body"):
                transformed_body = handler_config.request_transformer.transform_body(
                    transformed_body
                )

        return transformed_body, headers, is_streaming
