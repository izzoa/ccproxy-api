
"""Proxy service for orchestrating Claude API requests with business logic."""

import asyncio
import json
import os
import random
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import structlog
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.responses import Response
from typing_extensions import TypedDict

from ccproxy.config.settings import Settings
from ccproxy.core.codex_transformers import CodexRequestTransformer
from ccproxy.core.http import BaseProxyClient
from ccproxy.core.http_transformers import (
    HTTPRequestTransformer,
    HTTPResponseTransformer,
)
from ccproxy.services.model_info_service import get_model_info_service
from ccproxy.observability import (
    PrometheusMetrics,
    get_metrics,
    request_context,
    timed_operation,
)
from ccproxy.observability.access_logger import log_request_access
from ccproxy.observability.streaming_response import StreamingResponseWithLogging
from ccproxy.services.credentials.manager import CredentialsManager
from ccproxy.testing import RealisticMockResponseGenerator
from ccproxy.utils.simple_request_logger import (
    append_streaming_log,
    write_request_log,
)


if TYPE_CHECKING:
    from ccproxy.observability.context import RequestContext


class RequestData(TypedDict):
    """Typed structure for transformed request data."""

    method: str
    url: str
    headers: dict[str, str]
    body: bytes | None


class ResponseData(TypedDict):
    """Typed structure for transformed response data."""

    status_code: int
    headers: dict[str, str]
    body: bytes


logger = structlog.get_logger(__name__)


class ProxyService:
    """Claude-specific proxy orchestration with business logic.

    This service orchestrates the complete proxy flow including:
    - Authentication management
    - Request/response transformations
    - Metrics collection (future)
    - Error handling and logging

    Pure HTTP forwarding is delegated to BaseProxyClient.
    """

    SENSITIVE_HEADERS = {"authorization", "x-api-key", "cookie", "set-cookie"}

    def __init__(
        self,
        proxy_client: BaseProxyClient,
        credentials_manager: CredentialsManager,
        settings: Settings,
        proxy_mode: str = "full",
        target_base_url: str = "https://api.anthropic.com",
        metrics: PrometheusMetrics | None = None,
        app_state: Any = None,
    ) -> None:
        """Initialize the proxy service.

        Args:
            proxy_client: HTTP client for pure forwarding
            credentials_manager: Authentication manager
            settings: Application settings
            proxy_mode: Transformation mode - "minimal" or "full"
            target_base_url: Base URL for the target API
            metrics: Prometheus metrics collector (optional)
            app_state: FastAPI app state for accessing detection data
        """
        self.proxy_client = proxy_client
        self.credentials_manager = credentials_manager
        self.settings = settings
        self.proxy_mode = proxy_mode
        self.target_base_url = target_base_url.rstrip("/")
        self.metrics = metrics or get_metrics()
        self.app_state = app_state

        # Create concrete transformers
        self.request_transformer = HTTPRequestTransformer()
        self.response_transformer = HTTPResponseTransformer()
        self.codex_transformer = CodexRequestTransformer()

        # Create OpenAI adapter for stream transformation
        from ccproxy.adapters.openai.adapter import OpenAIAdapter

        self.openai_adapter = OpenAIAdapter()

    def _extract_request_metadata(
        self, body: bytes | None
    ) -> tuple[str | None, bool]:
        """Extract model identifier and streaming flag from request payload."""

        model: str | None = None
        streaming = False

        if not body:
            return model, streaming

        try:
            payload = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return model, streaming

        if not isinstance(payload, dict):
            return model, streaming

        model_value = payload.get("model")
        if isinstance(model_value, str) and model_value:
            model = model_value

        stream_value = payload.get("stream")
        if isinstance(stream_value, bool):
            streaming = stream_value

        return model, streaming

        # Create mock response generator for bypass mode
        self.mock_generator = RealisticMockResponseGenerator()

        # Cache environment-based configuration
        self._proxy_url = self._init_proxy_url()
        self._ssl_context = self._init_ssl_context()
        self._verbose_streaming = (
            os.environ.get("CCPROXY_VERBOSE_STREAMING", "false").lower() == "true"
        )
        self._verbose_api = (
            os.environ.get("CCPROXY_VERBOSE_API", "false").lower() == "true"
        )

    def _init_proxy_url(self) -> str | None:
        """Initialize proxy URL from environment variables."""
        # Check for standard proxy environment variables
        # For HTTPS requests, prioritize HTTPS_PROXY
        https_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        all_proxy = os.environ.get("ALL_PROXY")
        http_proxy = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")

        proxy_url = https_proxy or all_proxy or http_proxy

        if proxy_url:
            logger.debug("proxy_configured", proxy_url=proxy_url)

        return proxy_url

    def _init_ssl_context(self) -> str | bool:
        """Initialize SSL context configuration from environment variables."""
        # Check for custom CA bundle
        ca_bundle = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get(
            "SSL_CERT_FILE"
        )

        # Check if SSL verification should be disabled (NOT RECOMMENDED)
        ssl_verify = os.environ.get("SSL_VERIFY", "true").lower()

        if ca_bundle and Path(ca_bundle).exists():
            logger.info("ca_bundle_configured", ca_bundle=ca_bundle)
            return ca_bundle
        elif ssl_verify in ("false", "0", "no"):
            logger.warning("ssl_verification_disabled")
            return False
        else:
            logger.debug("ssl_verification_default")
            return True

    async def handle_request(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes | None = None,
        query_params: dict[str, str | list[str]] | None = None,
        timeout: float = 240.0,
        request: Request | None = None,  # Optional FastAPI Request object
    ) -> tuple[int, dict[str, str], bytes] | StreamingResponse:
        """Handle a proxy request with full business logic orchestration.

        Args:
            method: HTTP method
            path: Request path (without /unclaude prefix)
            headers: Request headers
            body: Request body
            query_params: Query parameters
            timeout: Request timeout in seconds
            request: Optional FastAPI Request object for accessing request context

        Returns:
            Tuple of (status_code, headers, body) or StreamingResponse for streaming

        Raises:
            HTTPException: If request fails
        """
        # Extract request metadata
        model, streaming = self._extract_request_metadata(body)
        endpoint = path.split("/")[-1] if path else "unknown"

        # Use existing context from request if available, otherwise create new one
        if request and hasattr(request, "state") and hasattr(request.state, "context"):
            # Use existing context from middleware
            ctx = request.state.context
            # Add service-specific metadata
            ctx.add_metadata(
                endpoint=endpoint,
                model=model,
                streaming=streaming,
                service_type="proxy_service",
            )
            # Create a context manager that preserves the existing context's lifecycle
            # This ensures __aexit__ is called for proper access logging
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def existing_context_manager() -> AsyncGenerator[Any, None]:
                try:
                    yield ctx
                finally:
                    # Let the existing context handle its own lifecycle
                    # The middleware or parent context will call __aexit__
                    pass

            context_manager: Any = existing_context_manager()
        else:
            # Create new context for observability
            context_manager = request_context(
                method=method,
                path=path,
                endpoint=endpoint,
                model=model,
                streaming=streaming,
                service_type="proxy_service",
                metrics=self.metrics,
            )

        async with context_manager as ctx:
            try:
                # 1. Authentication - get access token
                async with timed_operation("oauth_token", ctx.request_id):
                    logger.debug("oauth_token_retrieval_start")
                    access_token = await self._get_access_token()

                # 2. Request transformation
                async with timed_operation("request_transform", ctx.request_id):
                    injection_mode = (
                        self.settings.claude.system_prompt_injection_mode.value
                    )
                    logger.debug(
                        "request_transform_start",
                        system_prompt_injection_mode=injection_mode,
                    )
                    transformed_request = (
                        await self.request_transformer.transform_proxy_request(
                            method,
                            path,
                            headers,
                            body,
                            query_params,
                            access_token,
                            self.target_base_url,
                            self.app_state,
                            injection_mode,
                        )
                    )

                # 3. Check for bypass header to skip upstream forwarding
                bypass_upstream = (
                    headers.get("X-CCProxy-Bypass-Upstream", "").lower() == "true"
                )

                if bypass_upstream:
                    logger.debug("bypassing_upstream_forwarding_due_to_header")
                    # Determine message type from request body for realistic response generation
                    message_type = self._extract_message_type_from_body(body)

                    # Check if this will be a streaming response
                    should_stream = streaming or self._should_stream_response(
                        transformed_request["headers"]
                    )

                    # Determine response format based on original request path
                    is_openai_format = self.response_transformer._is_openai_request(
                        path
                    )

                    if should_stream:
                        return await self._generate_bypass_streaming_response(
                            model, is_openai_format, ctx, message_type
                        )
                    else:
                        return await self._generate_bypass_standard_response(
                            model, is_openai_format, ctx, message_type
                        )

                # 3. Forward request using proxy client
                logger.debug("request_forwarding_start", url=transformed_request["url"])

                # Check if this will be a streaming response
                should_stream = streaming or self._should_stream_response(
                    transformed_request["headers"]
                )

                if should_stream:
                    logger.debug("streaming_response_detected")
                    return await self._handle_streaming_request(
                        transformed_request, path, timeout, ctx
                    )
                else:
                    logger.debug("non_streaming_response_detected")

                # Log the outgoing request if verbose API logging is enabled
                await self._log_verbose_api_request(transformed_request, ctx)

                # Handle regular request
                async with timed_operation("api_call", ctx.request_id) as api_op:
                    start_time = time.perf_counter()

                    (
                        status_code,
                        response_headers,
                        response_body,
                    ) = await self.proxy_client.forward(
                        method=transformed_request["method"],
                        url=transformed_request["url"],
                        headers=transformed_request["headers"],
                        body=transformed_request["body"],
                        timeout=timeout,
                    )

                    end_time = time.perf_counter()
                    api_duration = end_time - start_time
                    api_op["duration_seconds"] = api_duration

                # Log the received response if verbose API logging is enabled
                await self._log_verbose_api_response(
                    status_code, response_headers, response_body, ctx
                )

                # 4. Response transformation
                async with timed_operation("response_transform", ctx.request_id):
                    logger.debug("response_transform_start")
                    # For error responses, transform to OpenAI format if needed
                    transformed_response: ResponseData
                    if status_code >= 400:
                        logger.info(
                            "upstream_error_received",
                            status_code=status_code,
                            has_body=bool(response_body),
                            content_length=len(response_body) if response_body else 0,
                        )

                        # Use transformer to handle error transformation (including OpenAI format)
                        transformed_response = (
                            await self.response_transformer.transform_proxy_response(
                                status_code,
                                response_headers,
                                response_body,
                                path,
                                self.proxy_mode,
                            )
                        )
                    else:
                        transformed_response = (
                            await self.response_transformer.transform_proxy_response(
                                status_code,
                                response_headers,
                                response_body,
                                path,
                                self.proxy_mode,
                            )
                        )

                # 5. Extract response metrics using direct JSON parsing
                tokens_input = tokens_output = cache_read_tokens = (
                    cache_write_tokens
                ) = cost_usd = None
                if transformed_response["body"]:
                    try:
                        response_data = json.loads(
                            transformed_response["body"].decode("utf-8")
                        )
                        usage = response_data.get("usage", {})
                        tokens_input = usage.get("input_tokens")
                        tokens_output = usage.get("output_tokens")
                        cache_read_tokens = usage.get("cache_read_input_tokens")
                        cache_write_tokens = usage.get("cache_creation_input_tokens")

                        # Calculate cost including cache tokens if we have tokens and model
                        from ccproxy.utils.cost_calculator import calculate_token_cost

                        cost_usd = calculate_token_cost(
                            tokens_input,
                            tokens_output,
                            model,
                            cache_read_tokens,
                            cache_write_tokens,
                        )
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        pass  # Keep all values as None if parsing fails

                # 6. Update context with response data
                ctx.add_metadata(
                    status_code=status_code,
                    tokens_input=tokens_input,
                    tokens_output=tokens_output,
                    cache_read_tokens=cache_read_tokens,
                    cache_write_tokens=cache_write_tokens,
                    cost_usd=cost_usd,
                )

                return (
                    transformed_response["status_code"],
                    transformed_response["headers"],
                    transformed_response["body"],
                )

            except Exception as e:
                ctx.add_metadata(error=e)
                raise

    async def handle_codex_request(
        self,
        method: str,
        path: str,
        session_id: str,
        access_token: str,
        request: Request,
        settings: Settings,
    ) -> StreamingResponse | Response:
        """Handle OpenAI Codex proxy request with request/response capture.

        Args:
            method: HTTP method
            path: Request path (e.g., "/responses" or "/{session_id}/responses")
            session_id: Resolved session ID
            access_token: OpenAI access token
            request: FastAPI request object
            settings: Application settings

        Returns:
            StreamingResponse or regular Response
        """
        # Read request body - check if already stored by middleware
        if hasattr(request.state, "body"):
            body = request.state.body
        else:
            body = await request.body()

        # Extract request metadata for observability
        model, streaming = self._extract_request_metadata(body)
        endpoint = path.split("/")[-1] if path else "unknown"

        # Use existing context from request if available, otherwise create new one
        if request and hasattr(request, "state") and hasattr(request.state, "context"):
            # Use existing context from middleware
            ctx = request.state.context
            # Add service-specific metadata
            ctx.add_metadata(
                endpoint=endpoint,
                model=model,
                streaming=streaming,
                service_type="codex",
                session_id=session_id,
            )
            # Create a context manager that preserves the existing context's lifecycle
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def existing_context_manager() -> AsyncGenerator[Any, None]:
                try:
                    yield ctx
                finally:
                    # Let the existing context handle its own lifecycle
                    pass

            context_manager: Any = existing_context_manager()
        else:
            # Create new context for observability
            context_manager = request_context(
                method=method,
                path=path,
                endpoint=endpoint,
                model=model,
                streaming=streaming,
                service_type="codex",
                session_id=session_id,
                metrics=self.metrics,
            )

        async with context_manager as ctx:
            try:
                # Parse request data to capture the instructions field and other metadata
                request_data = None
                try:
                    request_data = json.loads(body.decode("utf-8")) if body else {}
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    request_data = {}
                    logger.warning(
                        "codex_json_decode_failed",
                        error=str(e),
                        body_preview=body[:100].decode("utf-8", errors="replace")
                        if body
                        else None,
                        body_length=len(body) if body else 0,
                    )

                # Parse account_id from token if available
                import jwt

                account_id = "unknown"
                async with timed_operation("parse_account_id", ctx.request_id):
                    try:
                        decoded = jwt.decode(access_token, options={"verify_signature": False})
                        account_id = decoded.get(
                            "org_id", decoded.get("sub", decoded.get("account_id", "unknown"))
                        )
                    except Exception:
                        pass

                # Get Codex detection data from app state
                codex_detection_data = None
                if self.app_state and hasattr(self.app_state, "codex_detection_data"):
                    codex_detection_data = self.app_state.codex_detection_data

                # Transform request
                async with timed_operation("request_transform", ctx.request_id):
                    # Use CodexRequestTransformer to build request
                    original_headers = dict(request.headers)
                    transformed_request = await self.codex_transformer.transform_codex_request(
                        method=method,
                        path=path,
                        headers=original_headers,
                        body=body,
                        access_token=access_token,
                        session_id=session_id,
                        account_id=account_id,
                        codex_detection_data=codex_detection_data,
                        target_base_url=settings.codex.base_url,
                    )

                target_url = transformed_request["url"]
                headers = transformed_request["headers"]
                transformed_body = transformed_request["body"] or body

                # Parse transformed body for logging
                transformed_request_data = request_data
                if transformed_body and transformed_body != body:
                    try:
                        transformed_request_data = json.loads(
                            transformed_body.decode("utf-8")
                        )
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        transformed_request_data = request_data

                # Attach request-level metadata for observability/dynamic insight
                if isinstance(transformed_request_data, dict):
                    ctx.add_metadata(
                        requested_codex_model=transformed_request_data.get("model"),
                        requested_max_output_tokens=transformed_request_data.get(
                            "max_output_tokens"
                        ),
                        requested_temperature=transformed_request_data.get("temperature"),
                        requested_top_p=transformed_request_data.get("top_p"),
                    )

                    if settings.codex.enable_dynamic_model_info and transformed_request_data.get(
                        "model"
                    ):
                        try:
                            model_info_service = get_model_info_service()
                            model_capabilities = await model_info_service.get_model_capabilities(
                                transformed_request_data["model"]
                            )

                            ctx.add_metadata(
                                model_context_window=model_capabilities.get("max_tokens"),
                                model_max_output_tokens=model_capabilities.get(
                                    "max_output_tokens"
                                ),
                                model_supports_tools=model_capabilities.get(
                                    "supports_function_calling"
                                ),
                                model_supports_vision=model_capabilities.get(
                                    "supports_vision"
                                ),
                            )
                        except Exception as exc:
                            logger.debug(
                                "codex_dynamic_model_lookup_failed",
                                model=transformed_request_data.get("model"),
                                error=str(exc),
                            )

                # Use context request_id instead of generating new one
                request_id = ctx.request_id

                # Log Codex request (including instructions field and headers)
                await self._log_codex_request(
                    request_id=request_id,
                    method=method,
                    url=target_url,
                    headers=headers,
                    body_data=transformed_request_data,
                    session_id=session_id,
                )

                # Check if user explicitly requested streaming (from original request)
                user_requested_streaming = self.codex_transformer._is_streaming_request(
                    body
                )

                # Forward request to ChatGPT backend
                if user_requested_streaming:
                    # Handle streaming request with improved observability
                    async with timed_operation("api_call", ctx.request_id) as api_op:
                        # First, collect the response to check for errors
                        collected_chunks = []
                        chunk_count = 0
                        total_bytes = 0
                        response_status_code = 200
                        response_headers = {}

                        async def stream_codex_response() -> AsyncGenerator[bytes, None]:
                            nonlocal \
                                collected_chunks, \
                                chunk_count, \
                                total_bytes, \
                                response_status_code, \
                                response_headers

                            logger.debug(
                                "proxy_service_streaming_started",
                                request_id=request_id,
                                session_id=session_id,
                            )

                            async with (
                                httpx.AsyncClient(timeout=240.0) as client,
                                client.stream(
                                    method=method,
                                    url=target_url,
                                    headers=headers,
                                    content=transformed_body,
                                ) as response,
                            ):
                                # Capture response info for error checking
                                response_status_code = response.status_code
                                response_headers = dict(response.headers)

                                # Log response headers for streaming
                                await self._log_codex_response_headers(
                                    request_id=request_id,
                                    status_code=response.status_code,
                                    headers=dict(response.headers),
                                    stream_type="codex_sse",
                                )

                                # Check if upstream actually returned streaming
                                content_type = response.headers.get("content-type", "")
                                is_streaming = "text/event-stream" in content_type

                                if not is_streaming:
                                    logger.warning(
                                        "codex_expected_streaming_but_got_regular",
                                        content_type=content_type,
                                        status_code=response.status_code,
                                    )

                                async for chunk in response.aiter_bytes():
                                    chunk_count += 1
                                    chunk_size = len(chunk)
                                    total_bytes += chunk_size
                                    collected_chunks.append(chunk)

                                    logger.debug(
                                        "proxy_service_streaming_chunk",
                                        request_id=request_id,
                                        chunk_number=chunk_count,
                                        chunk_size=chunk_size,
                                        total_bytes=total_bytes,
                                    )

                                    yield chunk

                            logger.debug(
                                "proxy_service_streaming_complete",
                                request_id=request_id,
                                total_chunks=chunk_count,
                                total_bytes=total_bytes,
                            )

                            # Log the complete stream data after streaming finishes
                            await self._log_codex_streaming_complete(
                                request_id=request_id,
                                chunks=collected_chunks,
                            )

                        # Execute the stream generator to collect the response
                        generator_chunks = []
                        async for chunk in stream_codex_response():
                            generator_chunks.append(chunk)

                        # Record API call duration
                        end_time = time.perf_counter()
                        api_duration = end_time - api_op["start_time"]
                        api_op["duration_seconds"] = api_duration

                    # Now check if this should be an error response
                    content_type = response_headers.get("content-type", "")
                    if (
                        response_status_code >= 400
                        and "text/event-stream" not in content_type
                    ):
                        # Update context with error status
                        ctx.add_metadata(status_code=response_status_code)
                        
                        # Return error as regular Response with proper status code
                        error_content = b"".join(collected_chunks)
                        logger.warning(
                            "codex_returning_error_as_regular_response",
                            status_code=response_status_code,
                            content_type=content_type,
                            content_preview=error_content[:200].decode(
                                "utf-8", errors="replace"
                            ),
                        )
                        return Response(
                            content=error_content,
                            status_code=response_status_code,
                            headers=response_headers,
                        )

                    # Update context with success status
                    ctx.add_metadata(status_code=200)

                    # Return streaming response with logging
                    async def replay_stream() -> AsyncGenerator[bytes, None]:
                        for chunk in generator_chunks:
                            yield chunk

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
                            "cache-control": "no-cache",
                            "connection": "keep-alive",
                        }
                    )

                    return StreamingResponseWithLogging(
                        content=replay_stream(),
                        request_context=ctx,
                        metrics=self.metrics,
                        status_code=200,
                        headers=streaming_headers,
                    )
                else:
                    # Handle non-streaming request
                    async with timed_operation("api_call", ctx.request_id) as api_op:
                        start_time = time.perf_counter()
                        
                        async with httpx.AsyncClient(timeout=240.0) as client:
                            response = await client.request(
                                method=method,
                                url=target_url,
                                headers=headers,
                                content=transformed_body,
                            )
                        
                        end_time = time.perf_counter()
                        api_duration = end_time - start_time
                        api_op["duration_seconds"] = api_duration

                        # Check if upstream response is streaming (shouldn't happen)
                        content_type = response.headers.get("content-type", "")
                        transfer_encoding = response.headers.get("transfer-encoding", "")
                        upstream_is_streaming = "text/event-stream" in content_type or (
                            transfer_encoding == "chunked" and content_type == ""
                        )

                        logger.debug(
                            "codex_response_non_streaming",
                            content_type=content_type,
                            user_requested_streaming=user_requested_streaming,
                            upstream_is_streaming=upstream_is_streaming,
                            transfer_encoding=transfer_encoding,
                        )

                        if upstream_is_streaming:
                            # Upstream is streaming but user didn't request streaming
                            # Collect all streaming data and return as JSON
                            logger.debug(
                                "converting_upstream_stream_to_json", request_id=request_id
                            )

                            collected_chunks = []
                            async for chunk in response.aiter_bytes():
                                collected_chunks.append(chunk)

                            # Combine all chunks
                            full_content = b"".join(collected_chunks)

                            # Try to parse the streaming data and extract the final response
                            try:
                                # Parse SSE data to extract JSON response
                                content_str = full_content.decode("utf-8")
                                lines = content_str.strip().split("\n")

                                # Look for the last data line with JSON content
                                final_json = None
                                for line in reversed(lines):
                                    if line.startswith("data: ") and not line.endswith(
                                        "[DONE]"
                                    ):
                                        try:
                                            json_str = line[6:]  # Remove "data: " prefix
                                            final_json = json.loads(json_str)
                                            break
                                        except json.JSONDecodeError:
                                            continue

                                if final_json:
                                    response_content = json.dumps(final_json).encode(
                                        "utf-8"
                                    )
                                else:
                                    # Fallback: return the raw content
                                    response_content = full_content

                            except (UnicodeDecodeError, json.JSONDecodeError):
                                # Fallback: return raw content
                                response_content = full_content

                            # Log the complete response
                            try:
                                response_data = json.loads(response_content.decode("utf-8"))
                            except (json.JSONDecodeError, UnicodeDecodeError):
                                response_data = {
                                    "raw_content": response_content.decode(
                                        "utf-8", errors="replace"
                                    )
                                }

                            await self._log_codex_response(
                                request_id=request_id,
                                status_code=response.status_code,
                                headers=dict(response.headers),
                                body_data=response_data,
                            )

                            # Return as JSON response
                            return Response(
                                content=response_content,
                                status_code=response.status_code,
                                headers={
                                    "content-type": "application/json",
                                    "content-length": str(len(response_content)),
                                },
                                media_type="application/json",
                            )
                        else:
                            # For regular non-streaming responses
                            response_data = None
                            tokens_input = tokens_output = cost_usd = None
                            
                            try:
                                response_data = (
                                    json.loads(response.content.decode("utf-8"))
                                    if response.content
                                    else {}
                                )
                                
                                # Extract usage metrics if available
                                usage = response_data.get("usage", {})
                                if usage:
                                    tokens_input = usage.get("prompt_tokens") or usage.get("input_tokens")
                                    tokens_output = usage.get("completion_tokens") or usage.get("output_tokens")
                                    
                                    # Calculate cost if we have tokens and model
                                    if tokens_input and tokens_output and model:
                                        from ccproxy.utils.cost_calculator import calculate_token_cost
                                        cost_usd = calculate_token_cost(
                                            tokens_input,
                                            tokens_output,
                                            model,
                                            0,  # No cache tokens for Codex
                                            0
                                        )
                                        
                            except (json.JSONDecodeError, UnicodeDecodeError):
                                response_data = {
                                    "raw_content": response.content.decode(
                                        "utf-8", errors="replace"
                                    )
                                }

                            await self._log_codex_response(
                                request_id=request_id,
                                status_code=response.status_code,
                                headers=dict(response.headers),
                                body_data=response_data,
                            )

                            # Update context with response metadata
                            ctx.add_metadata(
                                status_code=response.status_code,
                                tokens_input=tokens_input,
                                tokens_output=tokens_output,
                                cost_usd=cost_usd,
                            )

                            # Return regular response
                            return Response(
                                content=response.content,
                                status_code=response.status_code,
                                headers=dict(response.headers),
                                media_type=response.headers.get("content-type"),
                            )

            except Exception as e:
                ctx.add_metadata(error=e)
                logger.error("Codex request failed", error=str(e), session_id=session_id)
                raise
