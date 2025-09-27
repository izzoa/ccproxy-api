"""HTTP client with hook support for request/response interception."""

import contextlib
import json as jsonlib
from collections.abc import AsyncIterator, Sequence
from typing import Any, cast

import httpx
from httpx._types import (
    HeaderTypes,
    QueryParamTypes,
    RequestContent,
    RequestData,
    RequestFiles,
)

from ccproxy.core.logging import get_logger
from ccproxy.core.plugins.hooks.events import HookEvent
from ccproxy.core.request_context import RequestContext
from ccproxy.utils.headers import (
    extract_response_headers,
)


logger = get_logger(__name__)


MAX_BODY_LOG_CHARS = 2048


def _stringify_body_for_logging(body: Any) -> tuple[str | None, int, bool]:
    """Convert a request/response body into a safe preview for logging."""

    if body is None:
        return None, 0, False

    try:
        if isinstance(body, bytes | bytearray | memoryview):
            text = bytes(body).decode("utf-8", errors="replace")
        elif isinstance(body, str):
            text = body
        else:
            text = jsonlib.dumps(body, ensure_ascii=False)
    except Exception:
        text = str(body)

    length = len(text)
    truncated = length > MAX_BODY_LOG_CHARS
    preview = f"{text[:MAX_BODY_LOG_CHARS]}...[truncated]" if truncated else text
    return preview, length, truncated


class HookableHTTPClient(httpx.AsyncClient):
    """HTTP client wrapper that emits hooks for all requests/responses."""

    def __init__(self, *args: Any, hook_manager: Any | None = None, **kwargs: Any):
        """Initialize HTTP client with optional hook support.

        Args:
            *args: Arguments for httpx.AsyncClient
            hook_manager: Optional HookManager instance for emitting hooks
            **kwargs: Keyword arguments for httpx.AsyncClient
        """
        super().__init__(*args, **kwargs)
        self.hook_manager = hook_manager

    @staticmethod
    def _normalize_header_pairs(
        headers: HeaderTypes | None,
    ) -> list[tuple[str, str]]:
        """Normalize various httpx header types into string pairs.

        Accepts mapping-like objects, httpx.Headers, or sequences of pairs.
        Ensures keys/values are converted to ``str`` and preserves order.
        """
        if not headers:
            return []
        try:
            if hasattr(headers, "items") and callable(headers.items):  # mapping/Headers
                return [(str(k), str(v)) for k, v in cast(Any, headers).items()]
            # Sequence of pairs
            return [
                (str(k), str(v)) for k, v in cast(Sequence[tuple[Any, Any]], headers)
            ]
        except Exception:
            return []

    async def request(
        self,
        method: str,
        url: httpx.URL | str,
        *,
        content: RequestContent | None = None,
        data: RequestData | None = None,
        files: RequestFiles | None = None,
        params: QueryParamTypes | None = None,
        headers: HeaderTypes | None = None,
        json: Any | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Make an HTTP request with hook emissions.

        Emits:
            - HTTP_REQUEST before sending
            - HTTP_RESPONSE after receiving response
            - HTTP_ERROR on errors
        """
        # Build request context for hooks
        request_context: dict[str, Any] = {
            "method": method,
            "url": str(url),
            "headers": dict(self._normalize_header_pairs(headers)),
            "is_provider_request": True,
            "origin": "upstream",
        }

        # Try to get current request ID from RequestContext
        try:
            current_context = RequestContext.get_current()
            if current_context and hasattr(current_context, "request_id"):
                request_context["request_id"] = current_context.request_id
        except Exception:
            # If no request context available, hooks will generate their own ID
            pass

        # Add body information
        if json is not None:
            request_context["body"] = json
            request_context["is_json"] = True
            preview, length, truncated = _stringify_body_for_logging(json)
        elif data is not None:
            request_context["body"] = data
            request_context["is_json"] = False
            preview, length, truncated = _stringify_body_for_logging(data)
        elif content is not None:
            # Handle content parameter - could be bytes, string, or other
            if isinstance(content, bytes | str):
                try:
                    if isinstance(content, bytes):
                        content_str = content.decode("utf-8")
                    else:
                        content_str = content

                    if content_str.strip().startswith(("{", "[")):
                        request_context["body"] = jsonlib.loads(content_str)
                        request_context["is_json"] = True
                    else:
                        request_context["body"] = content
                        request_context["is_json"] = False
                except Exception:
                    # If parsing fails, just include as-is
                    request_context["body"] = content
                    request_context["is_json"] = False
            else:
                request_context["body"] = content
                request_context["is_json"] = False
            preview, length, truncated = _stringify_body_for_logging(
                request_context["body"]
            )
        else:
            preview, length, truncated = (None, 0, False)

        logger.info(
            "upstream_http_request",
            method=method,
            url=str(url),
            request_id=request_context.get("request_id"),
            body_preview=preview,
            body_size=length,
            body_truncated=truncated,
            is_json=request_context.get("is_json", False),
            category="http",
        )

        # Emit pre-request hook
        if self.hook_manager:
            try:
                await self.hook_manager.emit(
                    HookEvent.HTTP_REQUEST,
                    request_context,
                )
            except Exception as e:
                logger.debug(
                    "http_request_hook_error",
                    error=str(e),
                    method=method,
                    url=str(url),
                )

        try:
            # Make the actual request
            response = await super().request(
                method,
                url,
                content=content,
                data=data,
                files=files,
                json=json,
                params=params,
                headers=headers,
                **kwargs,
            )

            # Emit post-response hook
            if self.hook_manager:
                # Read response content FIRST before any other processing
                response_content = response.content

                response_context = {
                    **request_context,  # Include request info
                    "status_code": response.status_code,
                    "response_headers": extract_response_headers(response),
                    "is_provider_response": True,
                }

                # Include response body from the content we just read
                try:
                    content_type = response.headers.get("content-type", "")
                    if "application/json" in content_type:
                        # Try to parse the raw content as JSON
                        try:
                            response_context["response_body"] = jsonlib.loads(
                                response_content.decode("utf-8")
                            )
                        except Exception:
                            # If JSON parsing fails, include as text
                            response_context["response_body"] = response_content.decode(
                                "utf-8", errors="replace"
                            )
                    else:
                        # For non-JSON content, include as text
                        response_context["response_body"] = response_content.decode(
                            "utf-8", errors="replace"
                        )
                except Exception:
                    # Last resort - include as bytes
                    response_context["response_body"] = response_content

                preview, length, truncated = _stringify_body_for_logging(
                    response_context.get("response_body")
                )
                logger.info(
                    "upstream_http_response",
                    url=str(url),
                    request_id=response_context.get("request_id"),
                    status_code=response.status_code,
                    body_preview=preview,
                    body_size=length,
                    body_truncated=truncated,
                    category="http",
                )

                try:
                    await self.hook_manager.emit(
                        HookEvent.HTTP_RESPONSE,
                        response_context,
                    )
                except Exception as e:
                    logger.debug(
                        "http_response_hook_error",
                        error=str(e),
                        status_code=response.status_code,
                    )

                try:
                    recreated_response = httpx.Response(
                        status_code=response.status_code,
                        headers=response.headers,
                        content=response_content,
                        request=response.request,
                    )
                    return recreated_response
                except Exception:
                    # If recreation fails, return original (may have empty body)
                    logger.debug("response_recreation_failed")
                    return response

            return response

        except Exception as error:
            # Emit error hook
            if self.hook_manager:
                error_context = {
                    **request_context,
                    "error_type": type(error).__name__,
                    "error_detail": str(error),
                }

                # Add response info if it's an HTTPStatusError
                if isinstance(error, httpx.HTTPStatusError):
                    error_context["status_code"] = error.response.status_code
                    error_context["response_body"] = error.response.text

                try:
                    await self.hook_manager.emit(
                        HookEvent.HTTP_ERROR,
                        error_context,
                    )
                except Exception as e:
                    logger.debug(
                        "http_error_hook_error",
                        error=str(e),
                        original_error=str(error),
                    )

            logger.error(
                "upstream_http_error",
                url=str(url),
                request_id=request_context.get("request_id"),
                error_type=type(error).__name__,
                error_detail=str(error),
                category="http",
            )

            # Re-raise the original error
            raise

    @contextlib.asynccontextmanager
    async def stream(
        self,
        method: str,
        url: httpx.URL | str,
        *,
        content: RequestContent | None = None,
        data: RequestData | None = None,
        files: RequestFiles | None = None,
        params: QueryParamTypes | None = None,
        headers: HeaderTypes | None = None,
        json: Any | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[httpx.Response]:
        """Make a streaming HTTP request with hook emissions.

        This method emits HTTP hooks for streaming requests, capturing the complete
        response body while maintaining streaming behavior.

        Emits:
            - HTTP_REQUEST before sending
            - HTTP_RESPONSE after receiving complete response
            - HTTP_ERROR on errors
        """
        # Build request context for hooks (same as request() method)
        request_context: dict[str, Any] = {
            "method": method,
            "url": str(url),
            "headers": dict(self._normalize_header_pairs(headers)),
            "is_provider_request": True,
            "origin": "upstream",
        }

        # Try to get current request ID from RequestContext
        try:
            current_context = RequestContext.get_current()
            if current_context and hasattr(current_context, "request_id"):
                request_context["request_id"] = current_context.request_id
        except Exception:
            # No current context available, that's OK
            pass

        # Add request body to context if available
        if json is not None:
            request_context["body"] = json
            request_context["is_json"] = True
        elif data is not None:
            request_context["body"] = data
            request_context["is_json"] = False
        elif content is not None:
            request_context["body"] = content
            request_context["is_json"] = False

        preview, length, truncated = _stringify_body_for_logging(
            request_context.get("body")
        )
        logger.info(
            "upstream_http_request",
            method=method,
            url=str(url),
            request_id=request_context.get("request_id"),
            body_preview=preview,
            body_size=length,
            body_truncated=truncated,
            is_json=request_context.get("is_json", False),
            streaming=True,
            category="http",
        )

        # Emit pre-request hook
        if self.hook_manager:
            try:
                await self.hook_manager.emit(
                    HookEvent.HTTP_REQUEST,
                    request_context,
                )
            except Exception as e:
                logger.debug(
                    "http_request_hook_error",
                    error=str(e),
                    method=method,
                    url=str(url),
                )

        try:
            # Start the streaming request
            async with super().stream(
                method=method,
                url=url,
                content=content,
                data=data,
                files=files,
                params=params,
                headers=headers,
                json=json,
                **kwargs,
            ) as response:
                # True streaming mode: do NOT pre-consume the upstream stream.
                # Emit a lightweight HTTP_RESPONSE hook with headers/status only,
                # then yield the original streaming response so downstream can
                # process bytes incrementally (no buffering).
                if self.hook_manager:
                    try:
                        response_context = {
                            **request_context,
                            "status_code": response.status_code,
                            "response_headers": extract_response_headers(response),
                            "is_provider_response": True,
                            # Indicate streaming; omit body to avoid buffering
                            "streaming": True,
                        }
                        await self.hook_manager.emit(
                            HookEvent.HTTP_RESPONSE,
                            response_context,
                        )
                    except Exception as e:
                        logger.debug(
                            "http_response_hook_error",
                            error=str(e),
                            status_code=response.status_code,
                        )

                # Yield the original streaming response (no pre-buffering)
                logger.info(
                    "upstream_http_response",
                    url=str(url),
                    request_id=request_context.get("request_id"),
                    status_code=response.status_code,
                    streaming=True,
                    body_preview=None,
                    body_size=0,
                    body_truncated=False,
                    category="http",
                )
                yield response

        except Exception as error:
            # Emit error hook
            if self.hook_manager:
                error_context = {
                    **request_context,
                    "error": error,
                    "error_type": type(error).__name__,
                }

                # Add response info if it's an HTTPStatusError
                if isinstance(error, httpx.HTTPStatusError):
                    error_context["status_code"] = error.response.status_code
                    error_context["response_body"] = error.response.text

                try:
                    await self.hook_manager.emit(
                        HookEvent.HTTP_ERROR,
                        error_context,
                    )
                except Exception as e:
                    logger.debug(
                        "http_error_hook_error",
                        error=str(e),
                        original_error=str(error),
                    )

            logger.error(
                "upstream_http_error",
                url=str(url),
                request_id=request_context.get("request_id"),
                error_type=type(error).__name__,
                error_detail=str(error),
                streaming=True,
                category="http",
            )

            # Re-raise the original error
            raise
