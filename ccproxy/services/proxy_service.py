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

# Import for unified dispatch
from ccproxy.adapters.base import APIAdapter
from ccproxy.config.settings import Settings
from ccproxy.core.http import BaseProxyClient
from ccproxy.core.http_transformers import (
    HTTPRequestTransformer,
    HTTPResponseTransformer,
)
from ccproxy.observability import (
    PrometheusMetrics,
    get_metrics,
)
from ccproxy.observability.access_logger import log_request_access
from ccproxy.observability.streaming_response import StreamingResponseWithLogging
from ccproxy.plugins.registry import PluginRegistry
from ccproxy.services.adapters.base import BaseAdapter
from ccproxy.services.credentials.manager import CredentialsManager
from ccproxy.services.provider_context import ProviderContext
from ccproxy.testing import RealisticMockResponseGenerator
from ccproxy.utils.simple_request_logger import (
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

        # Create OpenAI adapter for stream transformation
        from ccproxy.adapters.openai.adapter import OpenAIAdapter

        self.openai_adapter = OpenAIAdapter()

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

        # Initialize plugin registry
        self.plugin_registry = PluginRegistry()
        self._plugin_adapters: dict[str, BaseAdapter] = {}

        # Initialize plugins on startup (will be called explicitly)
        self._plugins_initialized = False

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

    async def _get_access_token(self) -> str:
        """Get access token for upstream authentication.

        Uses OAuth credentials from Claude CLI for upstream authentication.

        NOTE: The SECURITY__AUTH_TOKEN is only for authenticating incoming requests,
        not for upstream authentication.

        Returns:
            Valid access token

        Raises:
            HTTPException: If no valid token is available
        """
        # Always use OAuth credentials for upstream authentication
        # The SECURITY__AUTH_TOKEN is only for client authentication, not upstream
        try:
            access_token = await self.credentials_manager.get_access_token()
            if not access_token:
                logger.error("oauth_token_unavailable")

                # Try to get more details about credential status
                try:
                    validation = await self.credentials_manager.validate()

                    if (
                        validation.valid
                        and validation.expired
                        and validation.credentials
                    ):
                        logger.debug(
                            "oauth_token_expired",
                            expired_at=str(
                                validation.credentials.claude_ai_oauth.expires_at
                            ),
                        )
                except Exception as e:
                    logger.debug(
                        "credential_check_failed",
                        exc_info=e,
                    )

                raise HTTPException(
                    status_code=401,
                    detail="No valid OAuth credentials found. Please run 'ccproxy auth login'.",
                )

            logger.debug("oauth_token_retrieved")
            return access_token

        except HTTPException:
            raise
        except Exception as e:
            logger.error("oauth_token_retrieval_failed", exc_info=e)
            raise HTTPException(
                status_code=401,
                detail="Authentication failed",
            ) from e

    def _redact_headers(self, headers: dict[str, str]) -> dict[str, str]:
        """Redact sensitive information from headers for safe logging."""
        return {
            k: "[REDACTED]" if k.lower() in self.SENSITIVE_HEADERS else v
            for k, v in headers.items()
        }

    async def _log_verbose_api_request(
        self, request_data: RequestData, ctx: "RequestContext"
    ) -> None:
        """Log details of an outgoing API request if verbose logging is enabled."""
        if not self._verbose_api:
            return

        body = request_data.get("body")
        body_preview = ""
        full_body = None
        if body:
            try:
                full_body = body.decode("utf-8", errors="replace")
                # Truncate at 1024 chars for readability
                body_preview = full_body[:1024]
                # Try to parse as JSON for better formatting
                try:
                    import json

                    full_body = json.loads(full_body)
                except json.JSONDecodeError:
                    pass  # Keep as string
            except Exception:
                body_preview = f"<binary data of length {len(body)}>"

        logger.info(
            "verbose_api_request",
            method=request_data["method"],
            url=request_data["url"],
            headers=self._redact_headers(request_data["headers"]),
            body_size=len(body) if body else 0,
            body_preview=body_preview,
        )

        # Use new request logging system
        request_id = ctx.request_id
        timestamp = ctx.get_log_timestamp_prefix()
        await write_request_log(
            request_id=request_id,
            log_type="upstream_request",
            data={
                "method": request_data["method"],
                "url": request_data["url"],
                "headers": dict(request_data["headers"]),  # Don't redact in file
                "body": full_body,
            },
            timestamp=timestamp,
        )

    async def _log_verbose_api_response(
        self,
        status_code: int,
        headers: dict[str, str],
        body: bytes,
        ctx: "RequestContext",
    ) -> None:
        """Log details of a received API response if verbose logging is enabled."""
        if not self._verbose_api:
            return

        body_preview = ""
        if body:
            try:
                # Truncate at 1024 chars for readability
                body_preview = body.decode("utf-8", errors="replace")[:1024]
            except Exception:
                body_preview = f"<binary data of length {len(body)}>"

        logger.info(
            "verbose_api_response",
            status_code=status_code,
            headers=self._redact_headers(headers),
            body_size=len(body),
            body_preview=body_preview,
        )

        # Use new request logging system
        full_body = None
        if body:
            try:
                full_body_str = body.decode("utf-8", errors="replace")
                # Try to parse as JSON for better formatting
                try:
                    full_body = json.loads(full_body_str)
                except json.JSONDecodeError:
                    full_body = full_body_str
            except Exception:
                full_body = f"<binary data of length {len(body)}>"

        # Use new request logging system
        request_id = ctx.request_id
        timestamp = ctx.get_log_timestamp_prefix()
        await write_request_log(
            request_id=request_id,
            log_type="upstream_response",
            data={
                "status_code": status_code,
                "headers": dict(headers),  # Don't redact in file
                "body": full_body,
            },
            timestamp=timestamp,
        )

    async def _log_codex_request(
        self,
        request_id: str,
        method: str,
        url: str,
        headers: dict[str, str],
        body_data: dict[str, Any] | None,
        session_id: str,
    ) -> None:
        """Log outgoing Codex request preserving instructions field exactly."""
        if not self._verbose_api:
            return

        # Log to console with redacted headers
        logger.info(
            "verbose_codex_request",
            request_id=request_id,
            method=method,
            url=url,
            headers=self._redact_headers(headers),
            session_id=session_id,
            instructions_preview=(
                body_data.get("instructions", "")[:100] + "..."
                if body_data and body_data.get("instructions")
                else None
            ),
        )

        # Save complete request to file (without redaction)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        await write_request_log(
            request_id=request_id,
            log_type="codex_request",
            data={
                "method": method,
                "url": url,
                "headers": dict(headers),
                "body": body_data,
                "session_id": session_id,
            },
            timestamp=timestamp,
        )

    async def _log_codex_response(
        self,
        request_id: str,
        status_code: int,
        headers: dict[str, str],
        body_data: dict[str, Any] | None,
    ) -> None:
        """Log complete non-streaming Codex response."""
        if not self._verbose_api:
            return

        # Log to console with redacted headers
        logger.info(
            "verbose_codex_response",
            request_id=request_id,
            status_code=status_code,
            headers=self._redact_headers(headers),
            response_type="non_streaming",
        )

        # Save complete response to file
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        await write_request_log(
            request_id=request_id,
            log_type="codex_response",
            data={
                "status_code": status_code,
                "headers": dict(headers),
                "body": body_data,
            },
            timestamp=timestamp,
        )

    async def _log_codex_response_headers(
        self,
        request_id: str,
        status_code: int,
        headers: dict[str, str],
        stream_type: str,
    ) -> None:
        """Log streaming Codex response headers."""
        if not self._verbose_api:
            return

        # Log to console with redacted headers
        logger.info(
            "verbose_codex_response_headers",
            request_id=request_id,
            status_code=status_code,
            headers=self._redact_headers(headers),
            stream_type=stream_type,
        )

        # Save response headers to file
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        await write_request_log(
            request_id=request_id,
            log_type="codex_response_headers",
            data={
                "status_code": status_code,
                "headers": dict(headers),
                "stream_type": stream_type,
            },
            timestamp=timestamp,
        )

    async def _log_codex_streaming_complete(
        self,
        request_id: str,
        chunks: list[bytes],
    ) -> None:
        """Log complete streaming data after stream finishes."""
        if not self._verbose_api:
            return

        # Combine chunks and decode for analysis
        complete_data = b"".join(chunks)
        try:
            decoded_data = complete_data.decode("utf-8", errors="replace")
        except Exception:
            decoded_data = f"<binary data of length {len(complete_data)}>"

        # Log to console with preview
        logger.info(
            "verbose_codex_streaming_complete",
            request_id=request_id,
            total_bytes=len(complete_data),
            chunk_count=len(chunks),
            data_preview=decoded_data[:200] + "..."
            if len(decoded_data) > 200
            else decoded_data,
        )

        # Save complete streaming data to file
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        await write_request_log(
            request_id=request_id,
            log_type="codex_streaming_complete",
            data={
                "total_bytes": len(complete_data),
                "chunk_count": len(chunks),
                "complete_data": decoded_data,
            },
            timestamp=timestamp,
        )

    def _should_stream_response(self, headers: dict[str, str]) -> bool:
        """Check if response should be streamed based on request headers.

        Args:
            headers: Request headers

        Returns:
            True if response should be streamed
        """
        # Check if client requested streaming
        accept_header = headers.get("accept", "").lower()
        should_stream = (
            "text/event-stream" in accept_header or "stream" in accept_header
        )
        logger.debug(
            "stream_check_completed",
            accept_header=accept_header,
            should_stream=should_stream,
        )
        return should_stream

    def _extract_request_metadata(self, body: bytes | None) -> tuple[str | None, bool]:
        """Extract model and streaming flag from request body.

        Args:
            body: Request body

        Returns:
            Tuple of (model, streaming)
        """
        if not body:
            return None, False

        try:
            body_data = json.loads(body.decode("utf-8"))
            model = body_data.get("model")
            streaming = body_data.get("stream", False)
            return model, streaming
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None, False

    def _extract_message_type_from_body(self, body: bytes | None) -> str:
        """Extract message type from request body for realistic response generation."""
        if not body:
            return "short"

        try:
            body_data = json.loads(body.decode("utf-8"))
            # Check if tools are present - indicates tool use
            if body_data.get("tools"):
                return "tool_use"

            # Check message content length to determine type
            messages = body_data.get("messages", [])
            if messages:
                content = str(messages[-1].get("content", ""))
                if len(content) > 200:
                    return "long"
                elif len(content) < 50:
                    return "short"
                else:
                    return "medium"
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

        return "short"

    async def _generate_bypass_standard_response(
        self,
        model: str | None,
        is_openai_format: bool,
        ctx: "RequestContext",
        message_type: str = "short",
    ) -> tuple[int, dict[str, str], bytes]:
        """Generate realistic mock standard response."""

        # Check if we should simulate an error
        if self.mock_generator.should_simulate_error():
            error_response, status_code = self.mock_generator.generate_error_response(
                "openai" if is_openai_format else "anthropic"
            )
            response_body = json.dumps(error_response).encode()
            return status_code, {"content-type": "application/json"}, response_body

        # Generate realistic content and token counts
        content, input_tokens, output_tokens = (
            self.mock_generator.generate_response_content(
                message_type, model or "claude-3-5-sonnet-20241022"
            )
        )
        cache_read_tokens, cache_write_tokens = (
            self.mock_generator.generate_cache_tokens()
        )

        # Simulate realistic latency
        latency_ms = random.randint(*self.mock_generator.config.base_latency_ms)
        await asyncio.sleep(latency_ms / 1000.0)

        # Always start with Anthropic format
        request_id = f"msg_test_{ctx.request_id}_{random.randint(1000, 9999)}"
        content_list: list[dict[str, Any]] = [{"type": "text", "text": content}]
        anthropic_response = {
            "id": request_id,
            "type": "message",
            "role": "assistant",
            "content": content_list,
            "model": model or "claude-3-5-sonnet-20241022",
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_creation_input_tokens": cache_write_tokens,
                "cache_read_input_tokens": cache_read_tokens,
            },
        }

        # Add tool use if appropriate
        if message_type == "tool_use":
            content_list.insert(
                0,
                {
                    "type": "tool_use",
                    "id": f"toolu_{random.randint(10000, 99999)}",
                    "name": "calculator",
                    "input": {"expression": "23 * 45"},
                },
            )

        if is_openai_format:
            # Transform to OpenAI format using existing adapter
            openai_response = self.openai_adapter.adapt_response(anthropic_response)
            response_body = json.dumps(openai_response).encode()
        else:
            response_body = json.dumps(anthropic_response).encode()

        headers = {
            "content-type": "application/json",
            "content-length": str(len(response_body)),
        }

        # Update context with realistic metrics
        cost_usd = self.mock_generator.calculate_realistic_cost(
            input_tokens,
            output_tokens,
            model or "claude-3-5-sonnet-20241022",
            cache_read_tokens,
            cache_write_tokens,
        )

        ctx.add_metadata(
            status_code=200,
            tokens_input=input_tokens,
            tokens_output=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            cost_usd=cost_usd,
        )

        # Log comprehensive access log (includes Prometheus metrics)
        await log_request_access(
            context=ctx,
            status_code=200,
            method="POST",
            metrics=self.metrics,
        )

        return 200, headers, response_body

    async def _generate_bypass_streaming_response(
        self,
        model: str | None,
        is_openai_format: bool,
        ctx: "RequestContext",
        message_type: str = "short",
    ) -> StreamingResponse:
        """Generate realistic mock streaming response."""

        # Generate content and tokens
        content, input_tokens, output_tokens = (
            self.mock_generator.generate_response_content(
                message_type, model or "claude-3-5-sonnet-20241022"
            )
        )
        cache_read_tokens, cache_write_tokens = (
            self.mock_generator.generate_cache_tokens()
        )

        async def realistic_mock_stream_generator() -> AsyncGenerator[bytes, None]:
            request_id = f"msg_test_{ctx.request_id}_{random.randint(1000, 9999)}"

            if is_openai_format:
                # Generate OpenAI-style streaming
                chunks = await self._generate_realistic_openai_stream(
                    request_id,
                    model or "claude-3-5-sonnet-20241022",
                    content,
                    input_tokens,
                    output_tokens,
                )
            else:
                # Generate Anthropic-style streaming
                chunks = self.mock_generator.generate_realistic_anthropic_stream(
                    request_id,
                    model or "claude-3-5-sonnet-20241022",
                    content,
                    input_tokens,
                    output_tokens,
                    cache_read_tokens,
                    cache_write_tokens,
                )

            # Simulate realistic token generation rate
            tokens_per_second = self.mock_generator.config.token_generation_rate

            for i, chunk in enumerate(chunks):
                # Realistic delay based on token generation rate
                if i > 0:  # Don't delay the first chunk
                    # Estimate tokens in this chunk and calculate delay
                    chunk_tokens = len(str(chunk)) // 4  # Rough estimate
                    delay_seconds = chunk_tokens / tokens_per_second
                    # Add some randomness
                    delay_seconds *= random.uniform(0.5, 1.5)
                    await asyncio.sleep(max(0.01, delay_seconds))

                yield f"data: {json.dumps(chunk)}\n\n".encode()

            yield b"data: [DONE]\n\n"

        headers = {
            "content-type": "text/event-stream",
            "cache-control": "no-cache",
            "connection": "keep-alive",
        }

        # Update context with realistic metrics
        cost_usd = self.mock_generator.calculate_realistic_cost(
            input_tokens,
            output_tokens,
            model or "claude-3-5-sonnet-20241022",
            cache_read_tokens,
            cache_write_tokens,
        )

        ctx.add_metadata(
            status_code=200,
            tokens_input=input_tokens,
            tokens_output=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            cost_usd=cost_usd,
        )

        return StreamingResponseWithLogging(
            content=realistic_mock_stream_generator(),
            request_context=ctx,
            metrics=self.metrics,
            headers=headers,
        )

    async def _generate_realistic_openai_stream(
        self,
        request_id: str,
        model: str,
        content: str,
        input_tokens: int,
        output_tokens: int,
    ) -> list[dict[str, Any]]:
        """Generate realistic OpenAI streaming chunks by converting Anthropic format."""

        # Generate Anthropic chunks first
        anthropic_chunks = self.mock_generator.generate_realistic_anthropic_stream(
            request_id, model, content, input_tokens, output_tokens, 0, 0
        )

        # Convert to OpenAI format using the adapter
        openai_chunks = []
        for chunk in anthropic_chunks:
            # Use the OpenAI adapter to convert each chunk
            # This is a simplified conversion - in practice, you'd need a full streaming adapter
            if chunk.get("type") == "message_start":
                openai_chunks.append(
                    {
                        "id": f"chatcmpl-{request_id}",
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"role": "assistant", "content": ""},
                                "finish_reason": None,
                            }
                        ],
                    }
                )
            elif chunk.get("type") == "content_block_delta":
                delta_text = chunk.get("delta", {}).get("text", "")
                openai_chunks.append(
                    {
                        "id": f"chatcmpl-{request_id}",
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": delta_text},
                                "finish_reason": None,
                            }
                        ],
                    }
                )
            elif chunk.get("type") == "message_stop":
                openai_chunks.append(
                    {
                        "id": f"chatcmpl-{request_id}",
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    }
                )

        return openai_chunks

    # ==================== Unified Dispatch ====================

    async def dispatch_request(
        self,
        request: Request,
        provider_context: Any,  # Will be ProviderContext
    ) -> Response | StreamingResponse:
        """
        Unified request dispatcher for all providers.

        This method orchestrates the complete request lifecycle:
        1. Authentication
        2. Request transformation
        3. HTTP forwarding
        4. Response adaptation
        5. Streaming handling

        Args:
            request: FastAPI request object
            provider_context: Provider-specific configuration

        Returns:
            Response or StreamingResponse based on request type

        Raises:
            HTTPException: For various error conditions
        """
        import uuid

        from ccproxy.auth.exceptions import AuthenticationError

        # Start request tracking
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        request_context = getattr(request.state, "context", None)

        logger.info(
            "dispatch_request_start",
            provider=provider_context.provider_name,
            request_id=request_id,
            path=request.url.path,
        )

        try:
            # Step 1: Get authentication headers
            auth_headers = await provider_context.auth_manager.get_auth_headers()

            # Step 2: Read and transform request body
            original_body = await request.body()
            transformed_body = await self._transform_request_body(
                body=original_body,
                adapter=provider_context.request_adapter,
                request=request,
                provider_context=provider_context,
            )

            # Step 3: Prepare headers
            headers = await self._prepare_headers(
                request_headers=dict(request.headers),
                auth_headers=auth_headers,
                extra_headers=provider_context.extra_headers,
                provider_context=provider_context,
            )

            # Step 4: Build target URL
            target_url = self._build_target_url(
                base_url=provider_context.target_base_url,
                path=request.url.path,
                query=str(request.url.query) if request.url.query else None,
            )

            # Step 5: Determine if streaming is needed
            is_streaming = await self._should_stream(
                request_body=transformed_body,
                provider_context=provider_context,
            )

            logger.debug(
                "dispatch_request", target_url=target_url, is_streaming=is_streaming
            )
            # Step 6: Execute request
            response: Response | StreamingResponse
            if is_streaming and provider_context.supports_streaming:
                response = await self._handle_streaming_request_unified(
                    method=request.method,
                    url=target_url,
                    headers=headers,
                    body=transformed_body,
                    provider_context=provider_context,
                    request_context=request_context,
                )
            else:
                response = await self._handle_regular_request(
                    method=request.method,
                    url=target_url,
                    headers=headers,
                    body=transformed_body,
                    provider_context=provider_context,
                    request_context=request_context,
                )

            logger.info(
                "dispatch_request_complete",
                provider=provider_context.provider_name,
                request_id=request_id,
                streaming=is_streaming,
            )

            return response

        except AuthenticationError as e:
            logger.error(
                "dispatch_request_auth_error",
                provider=provider_context.provider_name,
                error=str(e),
            )
            raise HTTPException(status_code=401, detail=str(e)) from e
        except Exception as e:
            logger.error(
                "dispatch_request_error",
                provider=provider_context.provider_name,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise HTTPException(
                status_code=500, detail=f"Request dispatch failed: {str(e)}"
            ) from e

    async def _transform_request_body(
        self,
        body: bytes,
        adapter: "APIAdapter | None",
        request: Request,
        provider_context: "ProviderContext",
    ) -> bytes:
        """Transform request body using adapter and provider-specific transformations."""

        if not body:
            return body

        # First apply adapter transformation if provided
        if adapter:
            try:
                # Parse JSON body
                request_json = json.loads(body.decode("utf-8"))

                # Apply adapter transformation
                transformed_json = await adapter.adapt_request(request_json)

                # Convert back to bytes
                body = json.dumps(transformed_json).encode("utf-8")
            except Exception as e:
                logger.warning(
                    "adapter_transformation_failed",
                    error=str(e),
                    fallback="using_original_body",
                )

        # Then apply provider-specific transformations
        from ccproxy.services.transformation_helpers import (
            apply_claude_transformations,
            apply_codex_transformations,
            should_apply_claude_transformations,
            should_apply_codex_transformations,
        )

        if should_apply_claude_transformations(provider_context.provider_name):
            injection_mode = (
                self.settings.claude.system_prompt_injection_mode.value
                if self.settings
                else "minimal"
            )
            # Only transform body part (headers handled separately)
            body, _ = await apply_claude_transformations(
                body=body,
                headers={},  # Don't transform headers here
                access_token="",  # Not needed for body transformation
                app_state=self.app_state,
                injection_mode=injection_mode,
                proxy_mode=self.proxy_mode,
            )
        elif should_apply_codex_transformations(provider_context.provider_name):
            # Apply Codex instructions injection
            # Note: For Codex, we need to apply transformations even for native format
            body, _ = await apply_codex_transformations(
                body=body,
                headers={},  # Don't transform headers here
                access_token="",  # Not needed for body transformation
                session_id=getattr(provider_context, "session_id", "") or "",
                account_id=getattr(provider_context, "account_id", "") or "",
                app_state=self.app_state,
            )

        return body

    async def _prepare_headers(
        self,
        request_headers: dict[str, str],
        auth_headers: dict[str, str],
        extra_headers: dict[str, str],
        provider_context: "ProviderContext",
    ) -> dict[str, str]:
        """Prepare headers for the outbound request."""

        from ccproxy.services.transformation_helpers import (
            apply_claude_transformations,
            apply_codex_transformations,
            should_apply_claude_transformations,
            should_apply_codex_transformations,
        )

        # Extract access token from auth headers
        # For Claude, we use OAuth Bearer token authentication
        access_token = ""
        if "Authorization" in auth_headers:
            access_token = auth_headers["Authorization"].replace("Bearer ", "")
        # Note: x-api-key would be for direct API key auth, not OAuth

        # Apply provider-specific transformations
        if should_apply_claude_transformations(provider_context.provider_name):
            # Use Claude transformer for complete header preparation
            _, headers = await apply_claude_transformations(
                body=b"",  # Not needed for header transformation
                headers=request_headers,
                access_token=access_token,  # This is for OAuth Bearer token
                app_state=self.app_state,
                proxy_mode=self.proxy_mode,
            )
            # Add authentication headers (could be Bearer token OR x-api-key)
            headers.update(auth_headers)
        elif should_apply_codex_transformations(provider_context.provider_name):
            # Use Codex transformer for complete header preparation
            _, headers = await apply_codex_transformations(
                body=b"",  # Not needed for header transformation
                headers=request_headers,
                access_token=access_token,
                session_id=getattr(provider_context, "session_id", "") or "",
                account_id=getattr(provider_context, "account_id", "") or "",
                app_state=self.app_state,
            )
            # Add authentication headers
            headers.update(auth_headers)
        else:
            # Default: start with request headers
            headers = dict(request_headers)
            # Add authentication
            headers.update(auth_headers)

        # IMPORTANT: Always remove hop-by-hop headers and Content-Length for streaming
        # These headers should never be forwarded as they cause issues with streaming responses
        for header in [
            "host",
            "connection",
            "keep-alive",
            "transfer-encoding",
            "content-length",
        ]:
            headers.pop(header.lower(), None)
            # Also check for case variations
            headers.pop(header.title(), None)
            headers.pop(header.upper(), None)

        # Add extra headers on top (these take precedence)
        headers.update(extra_headers)

        # Apply request transformer if provided (for custom transformations)
        if hasattr(provider_context, "request_transformer") and callable(
            provider_context.request_transformer
        ):
            headers = provider_context.request_transformer(headers)

        return headers

    def _build_target_url(
        self,
        base_url: str,
        path: str,
        query: str | None,
    ) -> str:
        """Build the target URL for the request using direct path mappings.

        Route mappings:
        - /api/v1/chat/completions -> /v1/messages (converted by adapter)
        - /api/v1/messages -> /v1/messages
        - /v1/messages -> /v1/messages
        - /codex/responses -> /responses
        - /codex/{session_id}/responses -> /responses (session_id in request body)
        - /codex/chat/completions -> /responses
        """
        # Direct path mappings
        path_mappings = {
            # Anthropic API mappings
            "/api/v1/chat/completions": "/v1/messages?beta=true",  # Will be converted by adapter
            "/api/v1/messages": "/v1/messages?beta=true",
            "/v1/chat/completions": "/v1/messages",  # Will be converted by adapter
            "/v1/messages": "/v1/messages",
            # Codex API mappings
            "/codex/responses": "/responses",
            "/codex/chat/completions": "/responses",  # OpenAI format to Codex messages
        }

        # Check for direct mapping first
        if path in path_mappings:
            target_path = path_mappings[path]
        # Handle dynamic Codex session paths - strip session_id from path
        elif path.startswith("/codex/") and "/responses" in path:
            # /codex/{session_id}/responses -> /responses
            # Session ID is passed in request body, not in path
            target_path = "/responses"
        else:
            # Use path as-is if no mapping found
            target_path = path

        # Build URL
        url = f"{base_url.rstrip('/')}{target_path}"
        if query:
            url = f"{url}?{query}"

        return url

    async def _should_stream(
        self,
        request_body: bytes,
        provider_context: "ProviderContext",
    ) -> bool:
        """Determine if the request should be streamed."""

        if not provider_context.supports_streaming:
            return False

        try:
            body_json = json.loads(request_body.decode("utf-8"))
            return bool(body_json.get("stream", False))
        except Exception:
            return False

    async def _handle_streaming_request_unified(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
        provider_context: Any,
        request_context: Any,
    ) -> StreamingResponse:
        """Handle streaming request with response adaptation."""

        async def stream_generator() -> AsyncGenerator[bytes, None]:
            async with (
                httpx.AsyncClient(timeout=provider_context.timeout) as client,
                client.stream(
                    method=method,
                    url=url,
                    headers=headers,
                    content=body,
                ) as response,
            ):
                # Check for errors
                if response.status_code >= 400:
                    error_body = await response.aread()
                    yield self._format_error_response(
                        status_code=response.status_code,
                        body=error_body,
                        provider_context=provider_context,
                    )
                    return

                # Stream with adaptation if adapter provided
                if provider_context.response_adapter:
                    logger.debug(
                        "stream_adaptation_starting",
                        provider=provider_context.provider_name,
                        has_adapter=True,
                        adapter_type=type(provider_context.response_adapter).__name__,
                    )

                    buffer = b""  # Buffer for incomplete SSE events
                    chunk_count = 0
                    event_count = 0

                    async for chunk_bytes in response.aiter_bytes():
                        chunk_count += 1
                        logger.debug(
                            "stream_chunk_received",
                            chunk_number=chunk_count,
                            chunk_size=len(chunk_bytes),
                            buffer_size_before=len(buffer),
                            first_50_bytes=chunk_bytes[:50]
                            if len(chunk_bytes) > 0
                            else None,
                        )

                        # Add to buffer
                        buffer += chunk_bytes

                        # Process complete SSE events (separated by double newlines)
                        while b"\n\n" in buffer:
                            event_count += 1
                            # Split at the first complete event
                            event_bytes, buffer = buffer.split(b"\n\n", 1)

                            logger.debug(
                                "complete_sse_event_found",
                                event_number=event_count,
                                event_size=len(event_bytes),
                                remaining_buffer=len(buffer),
                            )

                            try:
                                # Decode the complete event
                                event_str = event_bytes.decode("utf-8")
                                logger.debug(
                                    "event_decoded",
                                    event_number=event_count,
                                    event_preview=event_str[:200],
                                )

                                # Skip empty events
                                if not event_str.strip():
                                    continue

                                # Check if this is a data event
                                if "data: " in event_str:
                                    # Extract data from the event
                                    for line in event_str.split("\n"):
                                        if line.startswith("data: "):
                                            data_str = line[6:].strip()
                                            if data_str == "[DONE]":
                                                logger.debug(
                                                    "done_marker_found",
                                                    event_number=event_count,
                                                )
                                                yield b"data: [DONE]\n\n"
                                            else:
                                                try:
                                                    chunk_json = json.loads(data_str)
                                                    logger.debug(
                                                        "json_parsed",
                                                        event_number=event_count,
                                                        json_keys=list(
                                                            chunk_json.keys()
                                                        )
                                                        if isinstance(chunk_json, dict)
                                                        else None,
                                                    )

                                                    # Create async generator for single chunk
                                                    async def single_chunk_gen(
                                                        data: Any = chunk_json,
                                                    ) -> AsyncGenerator[Any, None]:
                                                        yield data

                                                    # Adapt the chunk
                                                    logger.debug(
                                                        "adapting_event",
                                                        event_number=event_count,
                                                    )
                                                    async for adapted_chunk in provider_context.response_adapter.adapt_stream(
                                                        single_chunk_gen()
                                                    ):
                                                        adapted_json = json.dumps(
                                                            adapted_chunk
                                                        )
                                                        logger.debug(
                                                            "event_adapted",
                                                            event_number=event_count,
                                                            adapted_size=len(
                                                                adapted_json
                                                            ),
                                                        )
                                                        yield f"data: {adapted_json}\n\n".encode()

                                                except json.JSONDecodeError as je:
                                                    logger.debug(
                                                        "json_decode_error",
                                                        event_number=event_count,
                                                        error=str(je),
                                                        data_preview=data_str[:100],
                                                    )
                                                    # Pass through the original event
                                                    yield event_bytes + b"\n\n"
                                else:
                                    # Pass through non-data events
                                    logger.debug(
                                        "passing_through_non_data_event",
                                        event_number=event_count,
                                    )
                                    yield event_bytes + b"\n\n"

                            except Exception as e:
                                logger.warning(
                                    "stream_event_processing_failed",
                                    error=str(e),
                                    event_number=event_count,
                                )
                                # Pass through the original event
                                yield event_bytes + b"\n\n"

                    # Log final state
                    logger.debug(
                        "stream_adaptation_completed",
                        total_chunks=chunk_count,
                        total_events=event_count,
                        remaining_buffer_size=len(buffer),
                    )
                else:
                    # Pass through raw SSE stream
                    async for chunk in response.aiter_bytes():
                        yield chunk

        # Return streaming response with logging
        # Note: We explicitly set headers here and don't pass through Content-Length
        # from upstream to avoid "Too much data for declared Content-Length" errors
        # We set content-type via headers instead of media_type to avoid any
        # automatic Content-Length calculation
        return StreamingResponseWithLogging(
            content=stream_generator(),
            request_context=request_context,
            metrics=self.metrics,
            status_code=200,
            headers={
                "content-type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # Disable nginx buffering
            },
        )

    async def _handle_regular_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
        provider_context: "ProviderContext",
        request_context: Any,
    ) -> Response:
        """Handle non-streaming request with response adaptation."""

        async with httpx.AsyncClient(timeout=provider_context.timeout) as client:
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                content=body,
            )

            # Check for errors
            if response.status_code >= 400:
                raise HTTPException(
                    status_code=500,
                    detail=f"Request failed with status {response.status_code}: "
                    f"{response.text[:500]}",
                )

            # Get response body
            response_body = response.content

            # Apply response adapter if provided
            if provider_context.response_adapter:
                try:
                    response_json = json.loads(response_body.decode("utf-8"))
                    adapted_json = (
                        await provider_context.response_adapter.adapt_response(
                            response_json
                        )
                    )
                    response_body = json.dumps(adapted_json).encode("utf-8")
                except Exception as e:
                    logger.warning(
                        "response_adaptation_failed",
                        error=str(e),
                        fallback="using_original_response",
                    )

            # Build response
            return Response(
                content=response_body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.headers.get("content-type", "application/json"),
            )

    def _format_error_response(
        self,
        status_code: int,
        body: bytes,
        provider_context: "ProviderContext",
    ) -> bytes:
        """Format error response based on provider."""

        try:
            error_json = json.loads(body.decode("utf-8"))
        except Exception:
            error_json = {"error": {"message": body.decode("utf-8", errors="replace")}}

        # Apply adapter error formatting if available
        if provider_context.response_adapter and hasattr(
            provider_context.response_adapter, "format_error"
        ):
            error_json = provider_context.response_adapter.format_error(error_json)

        return f"data: {json.dumps(error_json)}\n\n".encode()

    async def initialize_plugins(self) -> None:
        """Initialize and load plugins.

        This method should be called during application startup to discover
        and register all available plugins.
        """
        if self._plugins_initialized:
            logger.debug("Plugins already initialized")
            return

        logger.info("Initializing plugin system")

        # Get plugin directory from settings or use default
        plugin_dir = Path(getattr(self.settings, "plugin_dir", "plugins"))

        # Discover and load plugins
        await self.plugin_registry.discover(plugin_dir)

        # Register plugin adapters
        for plugin_name in self.plugin_registry.list_plugins():
            adapter = self.plugin_registry.get_adapter(plugin_name)
            if adapter:
                self._plugin_adapters[plugin_name] = adapter
                logger.info(f"Registered plugin adapter: {plugin_name}")

        self._plugins_initialized = True
        logger.info(
            f"Plugin initialization complete. Loaded {len(self._plugin_adapters)} plugins"
        )

    def get_plugin_adapter(self, name: str) -> BaseAdapter | None:
        """Get a plugin adapter by name.

        Args:
            name: Plugin/provider name

        Returns:
            Plugin adapter or None if not found
        """
        return self._plugin_adapters.get(name)

    def list_active_providers(self) -> list[str]:
        """List all active providers from plugins.

        Returns:
            List of provider names
        """
        providers: list[str] = []

        # Plugin providers only - no hardcoded built-in providers
        providers.extend(self._plugin_adapters.keys())

        return providers

    async def close(self) -> None:
        """Close any resources held by the proxy service."""
        if self.proxy_client:
            await self.proxy_client.close()
        if self.credentials_manager:
            await self.credentials_manager.__aexit__(None, None, None)
