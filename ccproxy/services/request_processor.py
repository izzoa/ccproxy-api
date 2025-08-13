"""Request processing orchestration for proxy requests."""

import json
import time
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from ccproxy.observability import request_context, timed_operation


if TYPE_CHECKING:
    from ccproxy.core.http import BaseProxyClient
    from ccproxy.observability.context import RequestContext
    from ccproxy.services.credentials.manager import CredentialsManager
    from ccproxy.services.streaming_handler import StreamingHandler


logger = structlog.get_logger(__name__)


class RequestProcessor:
    """Handle request processing orchestration and business logic."""

    def __init__(
        self,
        proxy_client: "BaseProxyClient",
        credentials_manager: "CredentialsManager",
        streaming_handler: "StreamingHandler",
        request_transformer: Any,
        response_transformer: Any,
        openai_adapter: Any,
        mock_generator: Any,
        app_state: Any = None,
        proxy_mode: str = "full",
        target_base_url: str = "https://api.anthropic.com",
        metrics: Any = None,
        settings: Any = None,
    ):
        self.proxy_client = proxy_client
        self.credentials_manager = credentials_manager
        self.streaming_handler = streaming_handler
        self.request_transformer = request_transformer
        self.response_transformer = response_transformer
        self.openai_adapter = openai_adapter
        self.mock_generator = mock_generator
        self.app_state = app_state
        self.proxy_mode = proxy_mode
        self.target_base_url = target_base_url.rstrip("/")
        self.metrics = metrics
        self.settings = settings

    async def handle_request(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes | None = None,
        query_params: dict[str, str | list[str]] | None = None,
        timeout: float = 240.0,
        request: Request | None = None,
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
                service_type="request_processor",
            )
            # Create a context manager that preserves the existing context's lifecycle
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def existing_context_manager() -> AsyncGenerator[
                "RequestContext", None
            ]:
                try:
                    yield ctx
                finally:
                    # Let the existing context handle its own lifecycle
                    pass

            context_manager = existing_context_manager()
        else:
            # Create new context for observability
            context_manager = request_context(
                method=method,
                path=path,
                endpoint=endpoint,
                model=model,
                streaming=streaming,
                service_type="request_processor",
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
                        if self.settings
                        else "none"
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
                    return await self._handle_bypass_request(
                        body, streaming, transformed_request, path, model, ctx
                    )

                # 4. Forward request using proxy client
                logger.debug("request_forwarding_start", url=transformed_request["url"])

                # Check if this will be a streaming response
                should_stream = streaming or self._should_stream_response(
                    transformed_request["headers"]
                )

                if should_stream:
                    logger.debug("streaming_response_detected")
                    return await self.streaming_handler.handle_streaming_request(
                        transformed_request,
                        path,
                        timeout,
                        ctx,
                        self.response_transformer,
                        self.openai_adapter,
                        self.proxy_mode,
                        self.metrics,
                    )
                else:
                    logger.debug("non_streaming_response_detected")
                    return await self._handle_standard_request(
                        transformed_request, path, timeout, ctx
                    )

            except Exception as e:
                ctx.add_metadata(error=e)
                raise

    async def _handle_bypass_request(
        self,
        body: bytes | None,
        streaming: bool,
        transformed_request: dict[str, Any],
        path: str,
        model: str | None,
        ctx: "RequestContext",
    ) -> tuple[int, dict[str, str], bytes] | StreamingResponse:
        """Handle bypass request using mock generator."""
        # Determine message type from request body for realistic response generation
        message_type = self._extract_message_type_from_body(body)

        # Check if this will be a streaming response
        should_stream = streaming or self._should_stream_response(
            transformed_request["headers"]
        )

        # Determine response format based on original request path
        is_openai_format = self.response_transformer._is_openai_request(path)

        if should_stream:
            return await self._generate_bypass_streaming_response(
                model, is_openai_format, ctx, message_type
            )
        else:
            return await self._generate_bypass_standard_response(
                model, is_openai_format, ctx, message_type
            )

    async def _handle_standard_request(
        self,
        transformed_request: dict[str, Any],
        path: str,
        timeout: float,
        ctx: "RequestContext",
    ) -> tuple[int, dict[str, str], bytes]:
        """Handle standard (non-streaming) request."""
        # Log the outgoing request if verbose API logging is enabled
        from ccproxy.services.proxy_service import RequestData

        typed_request: RequestData = {
            "method": transformed_request["method"],
            "url": transformed_request["url"],
            "headers": transformed_request["headers"],
            "body": transformed_request["body"],
        }
        await self.streaming_handler._log_verbose_api_request(typed_request, ctx)

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
        await self.streaming_handler._log_verbose_api_response(
            status_code, response_headers, response_body, ctx
        )

        # Response transformation
        async with timed_operation("response_transform", ctx.request_id):
            logger.debug("response_transform_start")

            if status_code >= 400:
                logger.info(
                    "upstream_error_received",
                    status_code=status_code,
                    has_body=bool(response_body),
                    content_length=len(response_body) if response_body else 0,
                )

            # Use transformer to handle transformation (including OpenAI format)
            transformed_response = (
                await self.response_transformer.transform_proxy_response(
                    status_code,
                    response_headers,
                    response_body,
                    path,
                    self.proxy_mode,
                )
            )

        # Extract response metrics using direct JSON parsing
        tokens_input = tokens_output = cache_read_tokens = cache_write_tokens = (
            cost_usd
        ) = None
        if transformed_response["body"]:
            try:
                response_data = json.loads(transformed_response["body"].decode("utf-8"))
                usage = response_data.get("usage", {})
                tokens_input = usage.get("input_tokens")
                tokens_output = usage.get("output_tokens")
                cache_read_tokens = usage.get("cache_read_input_tokens")
                cache_write_tokens = usage.get("cache_creation_input_tokens")

                # Calculate cost including cache tokens if we have tokens and model
                from ccproxy.utils.cost_calculator import calculate_token_cost

                model = ctx.metadata.get("model")
                cost_usd = calculate_token_cost(
                    tokens_input,
                    tokens_output,
                    model,
                    cache_read_tokens,
                    cache_write_tokens,
                )
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass  # Keep all values as None if parsing fails

        # Update context with response data
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

    async def _get_access_token(self) -> str:
        """Get access token for upstream authentication."""
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
                        error=str(e),
                        exc_info=True,
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
            logger.error("oauth_token_retrieval_failed", error=str(e), exc_info=True)
            raise HTTPException(
                status_code=401,
                detail="Authentication failed",
            ) from e

    def _should_stream_response(self, headers: dict[str, str]) -> bool:
        """Check if response should be streamed based on request headers."""
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
        """Extract model and streaming flag from request body."""
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
        result = await self.mock_generator._generate_bypass_standard_response(
            model, is_openai_format, ctx, message_type
        )
        return result  # type: ignore[no-any-return]

    async def _generate_bypass_streaming_response(
        self,
        model: str | None,
        is_openai_format: bool,
        ctx: "RequestContext",
        message_type: str = "short",
    ) -> StreamingResponse:
        """Generate realistic mock streaming response."""
        result = await self.mock_generator._generate_bypass_streaming_response(
            model, is_openai_format, ctx, message_type
        )
        return result  # type: ignore[no-any-return]
