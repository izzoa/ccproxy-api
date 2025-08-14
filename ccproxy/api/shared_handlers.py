"""Shared utilities for route handlers to reduce code duplication."""

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.responses import Response
from structlog import get_logger

from ccproxy.api.responses import ProxyResponse


logger = get_logger()


async def parse_request_data(
    request: Request,
) -> tuple[bytes, dict[str, str], dict[str, str | list[str]] | None, str]:
    """Parse common request data needed by all route handlers.

    Returns:
        Tuple of (body, headers, query_params, service_path)
    """
    # Get request body
    body = await request.body()

    # Get headers and query params
    headers = dict(request.headers)
    query_params: dict[str, str | list[str]] | None = (
        dict(request.query_params) if request.query_params else None
    )

    # Strip the /api prefix from the path
    service_path = request.url.path.removeprefix("/api")

    return body, headers, query_params, service_path


def handle_response_errors(
    status_code: int,
    response_headers: dict[str, str],
    response_body: bytes,
    request: Request,
) -> ProxyResponse:
    """Handle error responses with proper header preservation.

    Args:
        status_code: HTTP status code
        response_headers: Response headers from upstream
        response_body: Response body from upstream
        request: FastAPI request object for state storage

    Returns:
        ProxyResponse with error details
    """
    # Store headers for preservation middleware
    request.state.preserve_headers = response_headers

    # Forward error response directly with headers
    return ProxyResponse(
        content=response_body,
        status_code=status_code,
        headers=response_headers,
        media_type=response_headers.get("content-type", "application/json"),
    )


def burld_streaming_response(
    response_body: bytes, response_headers: dict[str, str]
) -> StreamingResponse:
    """Build a streaming response from response body and headers.

    Args:
        response_body: Response body containing SSE data
        response_headers: Headers from upstream

    Returns:
        StreamingResponse with proper SSE formatting
    """

    # Return as streaming response
    async def stream_generator() -> AsyncIterator[bytes]:
        # Split the SSE data into chunks
        for line in response_body.decode().split("\\n"):
            if line.strip():
                yield f"{line}\\n".encode()

    # Start with the response headers from proxy service
    streaming_headers = response_headers.copy()

    logger.info("build_streaming_response")
    # Remove Content-Length header if present (incompatible with streaming)
    # Some upstream servers incorrectly set this for streaming responses
    streaming_headers.pop("content-length", None)
    streaming_headers.pop("Content-Length", None)

    # Ensure critical headers for streaming
    streaming_headers["Cache-Control"] = "no-cache"
    streaming_headers["Connection"] = "keep-alive"

    # Set content-type if not already set by upstream
    if "content-type" not in streaming_headers:
        streaming_headers["content-type"] = "text/event-stream"

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
        headers=streaming_headers,
    )


def build_proxy_response(
    status_code: int,
    response_headers: dict[str, str],
    response_body: bytes,
    request: Request,
    format_converter: Any = None,
) -> ProxyResponse:
    """Build a standard proxy response with optional format conversion.

    Args:
        status_code: HTTP status code
        response_headers: Response headers
        response_body: Response body
        request: FastAPI request object for state storage
        format_converter: Optional converter (e.g., OpenAIAdapter) for response transformation

    Returns:
        ProxyResponse with proper formatting
    """
    # Store headers for preservation middleware
    request.state.preserve_headers = response_headers

    # Apply format conversion if needed
    final_body = response_body
    if format_converter and status_code < 400:
        try:
            response_data = json.loads(response_body.decode())
            converted_data = format_converter.adapt_response(response_data)
            final_body = json.dumps(converted_data).encode()
        except (json.JSONDecodeError, UnicodeDecodeError):
            # Keep original body if conversion fails
            pass

    logger.error("proxy response")
    return ProxyResponse(
        content=final_body,
        status_code=status_code,
        headers=response_headers,
        media_type=response_headers.get("content-type", "application/json"),
    )


async def handle_proxy_request(
    request: Request,
    proxy_service: Any,
    format_converter: Any = None,
) -> StreamingResponse | Response:
    """Handle a proxy request with common error handling and response processing.

    Args:
        request: FastAPI request object
        proxy_service: ProxyService instance for handling the request
        format_converter: Optional format converter (e.g., OpenAIAdapter)

    Returns:
        StreamingResponse or Response

    Raises:
        HTTPException: For various error conditions
    """
    try:
        # Parse request data
        body, headers, query_params, service_path = await parse_request_data(request)

        # Handle the request using proxy service directly
        response = await proxy_service.handle_request(
            method=request.method,
            path=service_path,
            headers=headers,
            body=body,
            query_params=query_params,
            request=request,  # Pass the request object for context access
        )

        # Return appropriate response type
        if isinstance(response, StreamingResponse):
            # Already a streaming response
            return response
        else:
            # Tuple response - handle regular response
            status_code, response_headers, response_body = response

            if status_code >= 400:
                return handle_response_errors(
                    status_code, response_headers, response_body, request
                )

            # Always build a proxy response (streaming is handled elsewhere)
            return build_proxy_response(
                status_code,
                response_headers,
                response_body,
                request,
                format_converter,
            )

    except HTTPException:
        # Re-raise HTTPException as-is (including 401 auth errors)
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Internal server error: {str(e)}"
        ) from e
