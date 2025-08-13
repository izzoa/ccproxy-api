"""OpenAI Codex API routes."""

import json
import time
import uuid
from collections.abc import AsyncIterator

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.responses import Response

from ccproxy.adapters.openai.models import (
    OpenAIChatCompletionRequest,
    OpenAIChatCompletionResponse,
)
from ccproxy.adapters.openai.response_adapter import ResponseAdapter
from ccproxy.adapters.openai.codex_adapter import CodexAdapter
from ccproxy.api.dependencies import ProxyServiceDep
from ccproxy.auth.openai import OpenAITokenManager
from ccproxy.config.settings import Settings, get_settings
from ccproxy.core.errors import AuthenticationError, ProxyError
from ccproxy.observability.streaming_response import StreamingResponseWithLogging


logger = structlog.get_logger(__name__)

# Create router
router = APIRouter(prefix="/codex", tags=["codex"])


def get_token_manager() -> OpenAITokenManager:
    """Get OpenAI token manager dependency."""
    return OpenAITokenManager()


def resolve_session_id(
    path_session: str | None = None,
    header_session: str | None = None,
) -> str:
    """Resolve session ID with priority: path > header > generated."""
    return path_session or header_session or str(uuid.uuid4())


async def check_codex_enabled(settings: Settings = Depends(get_settings)) -> None:
    """Check if Codex is enabled."""
    if not settings.codex.enabled:
        raise HTTPException(
            status_code=503, detail="OpenAI Codex provider is not enabled"
        )


@router.post("/responses", response_model=None)
async def codex_responses(
    request: Request,
    proxy_service: ProxyServiceDep,
    settings: Settings = Depends(get_settings),
    token_manager: OpenAITokenManager = Depends(get_token_manager),
    _: None = Depends(check_codex_enabled),
) -> StreamingResponse | Response:
    """Create completion with auto-generated session_id.

    This endpoint creates a new completion request with an automatically
    generated session_id. Each request gets a unique session.
    """
    # Get session_id from header if provided
    header_session_id = request.headers.get("session_id")
    session_id = resolve_session_id(header_session=header_session_id)

    # Get and validate access token
    try:
        access_token = await token_manager.get_valid_token()
        if not access_token:
            raise HTTPException(
                status_code=401,
                detail="No valid OpenAI credentials found. Please authenticate first.",
            )
    except HTTPException:
        # Re-raise HTTPExceptions without chaining to avoid stack traces
        raise
    except Exception as e:
        logger.debug(
            "Failed to get OpenAI access token",
            error=str(e),
            error_type=type(e).__name__,
        )
        raise HTTPException(
            status_code=401, detail="Failed to retrieve valid credentials"
        ) from None

    try:
        # Handle the Codex request
        response = await proxy_service.handle_codex_request(
            method="POST",
            path="/responses",
            session_id=session_id,
            access_token=access_token,
            request=request,
            settings=settings,
        )
        return response
    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail=str(e)) from None
    except ProxyError as e:
        raise HTTPException(status_code=502, detail=str(e)) from None
    except Exception as e:
        logger.error("Unexpected error in codex_responses", error=str(e))
        raise HTTPException(status_code=500, detail="Internal server error") from None


@router.post("/{session_id}/responses", response_model=None)
async def codex_responses_with_session(
    session_id: str,
    request: Request,
    proxy_service: ProxyServiceDep,
    settings: Settings = Depends(get_settings),
    token_manager: OpenAITokenManager = Depends(get_token_manager),
    _: None = Depends(check_codex_enabled),
) -> StreamingResponse | Response:
    """Create completion with specific session_id.

    This endpoint creates a completion request using the provided session_id
    from the URL path. This allows for session-specific conversations.
    """
    # Get and validate access token
    try:
        access_token = await token_manager.get_valid_token()
        if not access_token:
            raise HTTPException(
                status_code=401,
                detail="No valid OpenAI credentials found. Please authenticate first.",
            )
    except HTTPException:
        # Re-raise HTTPExceptions without chaining to avoid stack traces
        raise
    except Exception as e:
        logger.debug(
            "Failed to get OpenAI access token",
            error=str(e),
            error_type=type(e).__name__,
        )
        raise HTTPException(
            status_code=401, detail="Failed to retrieve valid credentials"
        ) from None

    try:
        # Handle the Codex request with specific session_id
        response = await proxy_service.handle_codex_request(
            method="POST",
            path=f"/{session_id}/responses",
            session_id=session_id,
            access_token=access_token,
            request=request,
            settings=settings,
        )
        return response
    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail=str(e)) from None
    except ProxyError as e:
        raise HTTPException(status_code=502, detail=str(e)) from None
    except Exception as e:
        logger.error("Unexpected error in codex_responses_with_session", error=str(e))
        raise HTTPException(status_code=500, detail="Internal server error") from None


@router.post("/chat/completions", response_model=None)
async def codex_chat_completions(
    openai_request: OpenAIChatCompletionRequest,
    request: Request,
    proxy_service: ProxyServiceDep,
    settings: Settings = Depends(get_settings),
    token_manager: OpenAITokenManager = Depends(get_token_manager),
    _: None = Depends(check_codex_enabled),
) -> StreamingResponse | OpenAIChatCompletionResponse:
    """OpenAI-compatible chat completions endpoint for Codex.

    This endpoint accepts OpenAI chat/completions format and converts it
    to OpenAI Response API format before forwarding to the ChatGPT backend.
    """
    # Get session_id from header if provided, otherwise generate
    header_session_id = request.headers.get("session_id")
    session_id = resolve_session_id(header_session=header_session_id)

    # Get and validate access token
    try:
        access_token = await token_manager.get_valid_token()
        if not access_token:
            raise HTTPException(
                status_code=401,
                detail="No valid OpenAI credentials found. Please authenticate first.",
            )
    except HTTPException:
        # Re-raise HTTPExceptions without chaining to avoid stack traces
        raise
    except Exception as e:
        logger.debug(
            "Failed to get OpenAI access token",
            error=str(e),
            error_type=type(e).__name__,
        )
        raise HTTPException(
            status_code=401, detail="Failed to retrieve valid credentials"
        ) from None

    try:
        # Create adapter for format conversion
        adapter = ResponseAdapter()

        # Convert OpenAI Chat Completions format to Response API format
        response_request = adapter.chat_to_response_request(openai_request)

        # Convert the transformed request to bytes
        codex_body = response_request.model_dump_json().encode("utf-8")

        # Get request context from middleware
        request_context = getattr(request.state, "context", None)

        # Create a mock request object with the converted body
        class MockRequest:
            def __init__(self, original_request: Request, new_body: bytes) -> None:
                self.method = original_request.method
                self.url = original_request.url
                self.headers = dict(original_request.headers)
                self.headers["content-length"] = str(len(new_body))
                self.state = original_request.state
                self._body = new_body

            async def body(self) -> bytes:
                return self._body

        mock_request = MockRequest(request, codex_body)

        # For streaming requests, handle the transformation directly
        if openai_request.stream:
            # Make the request directly to get the raw streaming response
            from ccproxy.core.codex_transformers import CodexRequestTransformer

            # Transform the request
            transformer = CodexRequestTransformer()
            transformed_request = await transformer.transform_codex_request(
                method="POST",
                path="/responses",
                headers=dict(request.headers),
                body=codex_body,
                access_token=access_token,
                session_id=session_id,
                account_id="unknown",  # Will be extracted from token if needed
                codex_detection_data=getattr(
                    proxy_service.app_state, "codex_detection_data", None
                )
                if proxy_service.app_state
                else None,
                target_base_url=settings.codex.base_url,
            )

            # Convert Response API SSE stream to Chat Completions format
            response_headers = {}
            # Generate stream_id and timestamp outside the nested function to avoid closure issues
            stream_id = f"chatcmpl_{uuid.uuid4().hex[:29]}"
            created = int(time.time())

            # Create codex adapter instance
            codex_adapter = CodexAdapter()
            
            async def stream_codex_response() -> AsyncIterator[bytes]:
                """Stream and convert Response API to Chat Completions format using CodexAdapter."""
                async with (
                    httpx.AsyncClient(timeout=240.0) as client,
                    client.stream(
                        method="POST",
                        url=transformed_request["url"],
                        headers=transformed_request["headers"],
                        content=transformed_request["body"],
                    ) as response,
                ):
                    # Capture response headers for forwarding
                    nonlocal response_headers
                    response_headers = dict(response.headers)

                    logger.debug(
                        "codex_chat_response_headers",
                        status_code=response.status_code,
                        content_type=response.headers.get("content-type", ""),
                        url=str(response.url),
                    )

                    # Check for error response first
                    if response.status_code >= 400:
                        error_body = b""
                        async for chunk in response.aiter_bytes():
                            error_body += chunk

                        # Try to parse error message
                        error_message = "Request failed"
                        if error_body:
                            try:
                                error_data = json.loads(error_body.decode("utf-8"))
                                if "detail" in error_data:
                                    error_message = error_data["detail"]
                                elif "error" in error_data and isinstance(
                                    error_data["error"], dict
                                ):
                                    error_message = error_data["error"].get(
                                        "message", "Request failed"
                                    )
                            except json.JSONDecodeError:
                                pass

                        logger.warning(
                            "codex_chat_error_response",
                            status_code=response.status_code,
                            error_message=error_message,
                        )

                        # Return error in streaming format using adapter
                        error_response = {
                            "error": {
                                "message": error_message,
                                "type": "invalid_request_error",
                                "code": response.status_code,
                            }
                        }
                        converted_error = codex_adapter.convert_error_to_chat_format(error_response)
                        yield f"data: {json.dumps(converted_error)}\n\n".encode()
                        return

                    # Use the CodexAdapter to handle streaming conversion
                    logger.debug(
                        "codex_stream_conversion_started_with_adapter",
                        session_id=session_id,
                        request_id=getattr(request.state, "request_id", "unknown"),
                    )

                    try:
                        # Convert the httpx streaming response to the format expected by CodexAdapter
                        async for chunk_dict in codex_adapter.convert_response_stream_to_chat(
                            response.aiter_bytes()
                        ):
                            # Convert chunk dict to SSE format
                            yield f"data: {json.dumps(chunk_dict)}\n\n".encode()

                        # Send final [DONE] message
                        yield b"data: [DONE]\n\n"

                    except Exception as e:
                        logger.error(
                            "codex_stream_conversion_error",
                            error=str(e),
                            session_id=session_id,
                        )
                        # Send error response in streaming format
                        error_response = {
                            "error": {
                                "message": f"Stream conversion error: {str(e)}",
                                "type": "stream_conversion_error",
                                "code": 502,
                            }
                        }
                        yield f"data: {json.dumps(error_response)}\n\n".encode()
                        yield b"data: [DONE]\n\n"

            # Execute the generator first to capture headers
            generator_chunks = []
            async for chunk in stream_codex_response():
                generator_chunks.append(chunk)

            # Forward upstream headers but filter out incompatible ones for streaming
            streaming_headers = dict(response_headers)
            # Remove headers that conflict with streaming responses
            streaming_headers.pop("content-length", None)
            streaming_headers.pop("content-encoding", None)
            streaming_headers.pop("date", None)
            # Set streaming-specific headers
            streaming_headers.update(
                {
                    "content-type": "text/event-stream",
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                }
            )

            # Replay the collected chunks
            async def replay_stream() -> AsyncIterator[bytes]:
                for chunk in generator_chunks:
                    yield chunk

            # Return streaming response with proper headers - handle missing request_context
            from ccproxy.observability.context import RequestContext

            # Create a minimal request context if none exists
            if request_context is None:
                request_context = RequestContext(
                    request_id=str(uuid.uuid4()),
                    start_time=time.perf_counter(),
                    logger=logger,
                )

            return StreamingResponseWithLogging(
                content=replay_stream(),
                request_context=request_context,
                metrics=getattr(proxy_service, "metrics", None),
                status_code=200,
                media_type="text/event-stream",
                headers=streaming_headers,
            )
        else:
            # Handle non-streaming request using the proxy service
            # Cast MockRequest to Request to satisfy type checker
            mock_request_typed: Request = mock_request  # type: ignore[assignment]
            response = await proxy_service.handle_codex_request(
                method="POST",
                path="/responses",
                session_id=session_id,
                access_token=access_token,
                request=mock_request_typed,
                settings=settings,
            )

            # Check if this is a streaming response (shouldn't happen for non-streaming requests)
            is_streaming_response = isinstance(response, StreamingResponse)

            if is_streaming_response and not openai_request.stream:
                # User requested non-streaming but backend returned streaming
                # Consume the stream and convert to non-streaming response
                accumulated_content = ""
                final_response = None

                error_response = None
                accumulated_chunks = ""

                async for chunk in response.body_iterator:  # type: ignore
                    chunk_str = chunk.decode("utf-8")
                    accumulated_chunks += chunk_str

                    # The Response API sends SSE events, but errors might be plain JSON
                    lines = chunk_str.strip().split("\n")
                    for line in lines:
                        if line.startswith("data:") and "[DONE]" not in line:
                            data_str = line[5:].strip()
                            try:
                                event_data = json.loads(data_str)
                                # Look for the completed response
                                if event_data.get("type") == "response.completed":
                                    final_response = event_data
                                # Also check if this is a direct error response (not SSE format)
                                elif (
                                    "detail" in event_data and "type" not in event_data
                                ):
                                    error_response = event_data
                            except json.JSONDecodeError:
                                continue

                # If we didn't find SSE events, try parsing the entire accumulated content as JSON
                if (
                    not final_response
                    and not error_response
                    and accumulated_chunks.strip()
                ):
                    try:
                        # Try to parse the entire content as JSON (for non-SSE error responses)
                        json_response = json.loads(accumulated_chunks.strip())
                        if (
                            "detail" in json_response
                            or "error" in json_response
                            or "message" in json_response
                        ):
                            error_response = json_response
                        else:
                            # Might be a valid response without SSE formatting
                            final_response = {"response": json_response}
                    except json.JSONDecodeError:
                        # Not valid JSON either
                        pass

                if final_response:
                    # Convert to Chat Completions format
                    return adapter.response_to_chat_completion(final_response)
                elif error_response:
                    # Handle error response
                    error_message = "Request failed"
                    if "detail" in error_response:
                        error_message = error_response["detail"]
                    elif "error" in error_response:
                        if isinstance(error_response["error"], dict):
                            error_message = error_response["error"].get(
                                "message", "Request failed"
                            )
                        else:
                            error_message = str(error_response["error"])
                    elif "message" in error_response:
                        error_message = error_response["message"]

                    # Log the error for debugging
                    logger.error(
                        "codex_streaming_error_response",
                        error_data=error_response,
                        error_message=error_message,
                    )

                    raise HTTPException(status_code=400, detail=error_message)
                else:
                    raise HTTPException(
                        status_code=502, detail="Failed to parse streaming response"
                    )
            else:
                # Non-streaming response - parse and convert
                if isinstance(response, Response):
                    # Check if this is an error response
                    if response.status_code >= 400:
                        # Return the error response as-is
                        error_body = response.body
                        if error_body:
                            try:
                                # Handle bytes/memoryview union
                                error_body_bytes = (
                                    bytes(error_body)
                                    if isinstance(error_body, memoryview)
                                    else error_body
                                )
                                error_data = json.loads(
                                    error_body_bytes.decode("utf-8")
                                )
                                # Log the actual error from backend
                                logger.error(
                                    "codex_backend_error",
                                    status_code=response.status_code,
                                    error_data=error_data,
                                )
                                # Pass through the error from backend
                                # Handle different error formats from backend
                                error_message = "Request failed"
                                if "detail" in error_data:
                                    error_message = error_data["detail"]
                                elif "error" in error_data:
                                    if isinstance(error_data["error"], dict):
                                        error_message = error_data["error"].get(
                                            "message", "Request failed"
                                        )
                                    else:
                                        error_message = str(error_data["error"])
                                elif "message" in error_data:
                                    error_message = error_data["message"]

                                raise HTTPException(
                                    status_code=response.status_code,
                                    detail=error_message,
                                )
                            except (json.JSONDecodeError, UnicodeDecodeError):
                                # Handle bytes/memoryview union for logging
                                error_body_bytes = (
                                    bytes(error_body)
                                    if isinstance(error_body, memoryview)
                                    else error_body
                                )
                                logger.error(
                                    "codex_backend_error_parse_failed",
                                    status_code=response.status_code,
                                    body=error_body_bytes[:500].decode(
                                        "utf-8", errors="replace"
                                    ),
                                )
                                pass
                        raise HTTPException(
                            status_code=response.status_code, detail="Request failed"
                        )

                    # Read the response body for successful responses
                    response_body = response.body
                    if response_body:
                        try:
                            # Handle bytes/memoryview union
                            response_body_bytes = (
                                bytes(response_body)
                                if isinstance(response_body, memoryview)
                                else response_body
                            )
                            response_data = json.loads(
                                response_body_bytes.decode("utf-8")
                            )
                            # Convert Response API format to Chat Completions format
                            return adapter.response_to_chat_completion(response_data)
                        except (json.JSONDecodeError, UnicodeDecodeError) as e:
                            logger.error("Failed to parse Codex response", error=str(e))
                            raise HTTPException(
                                status_code=502,
                                detail="Invalid response from Codex API",
                            ) from e

                # If we can't convert, return error
                raise HTTPException(
                    status_code=502, detail="Unable to process Codex response"
                )

    except HTTPException:
        raise
    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail=str(e)) from None
    except ProxyError as e:
        raise HTTPException(status_code=502, detail=str(e)) from None
    except Exception as e:
        logger.error("Unexpected error in codex_chat_completions", error=str(e))
        raise HTTPException(status_code=500, detail="Internal server error") from None


# NOTE: Test endpoint commented out after exploration
# Testing revealed that ChatGPT backend API only supports /responses endpoint
# and does NOT support OpenAI-style /chat/completions or other endpoints.
# See codex_endpoint_test_results.md for full findings.
#
# @router.api_route("/test/{path:path}", methods=["GET", "POST", "PUT", "DELETE"], response_model=None, include_in_schema=False)
# async def codex_test_probe(
#     path: str,
#     request: Request,
#     proxy_service: ProxyServiceDep,
#     settings: Settings = Depends(get_settings),
#     token_manager: OpenAITokenManager = Depends(get_token_manager),
#     _: None = Depends(check_codex_enabled),
# ) -> Response:
#     """Test endpoint to probe upstream ChatGPT backend API paths.
#
#     WARNING: This is a test endpoint for exploration only.
#     It forwards requests to any path on the ChatGPT backend API.
#     Should be removed or protected after testing.
#     """
#     # Get and validate access token
#     try:
#         access_token = await token_manager.get_valid_token()
#         if not access_token:
#             raise HTTPException(
#                 status_code=401,
#                 detail="No valid OpenAI credentials found. Please authenticate first.",
#             )
#     except Exception as e:
#         logger.error("Failed to get OpenAI access token", error=str(e))
#         raise HTTPException(
#             status_code=401, detail="Failed to retrieve valid credentials"
#         ) from e
#
#     # Log the test request
#     logger.info(f"Testing upstream path: /{path}", method=request.method)
#
#     try:
#         # Use a simple session_id for testing
#         session_id = "test-probe"
#
#         # Handle the test request - forward to the specified path
#         response = await proxy_service.handle_codex_request(
#             method=request.method,
#             path=f"/{path}",
#             session_id=session_id,
#             access_token=access_token,
#             request=request,
#             settings=settings,
#         )
#
#         logger.info(f"Test probe response for /{path}", status_code=getattr(response, "status_code", 200))
#         return response
#     except AuthenticationError as e:
#         logger.warning(f"Auth error for path /{path}: {str(e)}")
#         raise HTTPException(status_code=401, detail=str(e)) from None from e
#     except ProxyError as e:
#         logger.warning(f"Proxy error for path /{path}: {str(e)}")
#         raise HTTPException(status_code=502, detail=str(e)) from None from e
#     except Exception as e:
#         logger.error(f"Unexpected error testing path /{path}", error=str(e))
#         raise HTTPException(status_code=500, detail=f"Error testing path: {str(e)}") from e
