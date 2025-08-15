"""Codex adapter implementation for the plugin system."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import httpx
import structlog
from fastapi import Request
from starlette.responses import Response, StreamingResponse

from ccproxy.adapters.openai.adapter import OpenAIAdapter
from ccproxy.services.adapters.base import BaseAdapter

from .transformers import CodexRequestTransformer, CodexResponseTransformer


if TYPE_CHECKING:
    from ccproxy.auth.openai import OpenAITokenManager

    from .detection_service import CodexDetectionService


logger = structlog.get_logger(__name__)


class CodexAdapter(BaseAdapter):
    """Codex adapter for the plugin system.

    This adapter wraps the core CodexAdapter functionality and provides
    the interface required by the plugin system's BaseAdapter protocol.
    """

    def __init__(self, http_client: httpx.AsyncClient, logger: structlog.BoundLogger):
        """Initialize the Codex adapter."""
        super().__init__()
        # Create our own HTTP client for streaming
        # The passed client might be closed or shared
        self._http_client = httpx.AsyncClient(timeout=60.0)
        self._logger = logger
        self._core_adapter = OpenAIAdapter()
        
        # Initialize transformers
        self._request_transformer = CodexRequestTransformer()
        self._response_transformer = CodexResponseTransformer()
        
        self._auth_manager: OpenAITokenManager | None = None  # Will be set by plugin
        self._detection_service: CodexDetectionService | None = (
            None  # Will be set by plugin
        )

    async def handle_request(
        self, request: Request, endpoint: str, method: str, **kwargs: Any
    ) -> Response:
        """Handle a provider-specific request.

        Makes actual HTTP request to Codex API with proper transformation.
        """
        import json

        from ccproxy.auth.openai import OpenAITokenManager

        self._logger.debug(
            "codex_adapter_handle_request",
            endpoint=endpoint,
            method=method,
            has_kwargs=bool(kwargs),
        )

        try:
            # Get request body
            body = await request.body()
            if body:
                request_data = json.loads(body)
            else:
                request_data = {}

            # Get session_id from kwargs or generate
            session_id = kwargs.get("session_id") or str(__import__("uuid").uuid4())

            # Get Codex instructions if available
            codex_instructions = None
            try:
                codex_instructions = await self._get_codex_instructions()
            except Exception as e:
                self._logger.warning(
                    "codex_instructions_not_available",
                    reason=str(e),
                    error_type=type(e).__name__,
                    msg="Proceeding without Codex instructions",
                )
            
            # Transform request using the request transformer
            if "messages" in request_data:
                # Convert OpenAI format to Codex format
                transformed_request = self._request_transformer.transform_chat_to_codex(
                    request_data,
                    codex_instructions=codex_instructions,
                )
            else:
                # Native Codex format - just validate/enhance
                transformed_request = self._request_transformer.validate_codex_request(
                    request_data,
                    codex_instructions=codex_instructions,
                )

            # Build target URL - Codex API base URL
            # Session is passed in the request data, not in the URL
            base_url = "https://chatgpt.com"
            target_url = f"{base_url}/backend-api/codex/responses"

            self._logger.info(
                "codex_adapter_target_url",
                target_url=target_url,
                session_id=session_id,
                endpoint=endpoint,
            )

            # Prepare headers using transformer
            auth_headers = {}
            if self._auth_manager:
                self._logger.info(
                    "codex_auth_manager_check",
                    has_auth_manager=True,
                    auth_manager_type=type(self._auth_manager).__name__,
                    is_openai_manager=isinstance(
                        self._auth_manager, OpenAITokenManager
                    ),
                )
                try:
                    if isinstance(self._auth_manager, OpenAITokenManager):
                        auth_headers = await self._auth_manager.get_auth_headers()
                        self._logger.info(
                            "codex_auth_headers_added",
                            auth_header_keys=list(auth_headers.keys()),
                            has_authorization=("authorization" in auth_headers),
                        )
                except Exception as e:
                    self._logger.error(
                        "codex_auth_headers_failed",
                        error=str(e),
                        error_type=type(e).__name__,
                    )
            else:
                self._logger.warning(
                    "codex_auth_manager_missing", msg="No auth manager set"
                )
            
            # Use transformer to prepare headers
            headers = self._request_transformer.prepare_headers(
                dict(request.headers),
                session_id=session_id,
                auth_headers=auth_headers,
            )

            # Log final headers (mask auth token)
            headers_to_log = dict(headers.items())
            if "authorization" in headers_to_log:
                auth_val = headers_to_log["authorization"]
                if auth_val.startswith("Bearer "):
                    headers_to_log["authorization"] = (
                        f"Bearer {auth_val[7:27]}..."
                        if len(auth_val) > 27
                        else "Bearer [SHORT]"
                    )

            self._logger.info(
                "codex_request_headers",
                headers=headers_to_log,
                has_auth="authorization" in headers,
                method=method,
                url=target_url,
            )

            # Log the request body being sent
            self._logger.info(
                "codex_request_body",
                has_instructions="instructions" in transformed_request,
                has_input="input" in transformed_request,
                model=transformed_request.get("model"),
                stream=transformed_request.get("stream", False),
                body_keys=list(transformed_request.keys()),
                body_preview=str(transformed_request)[:500]
                if transformed_request
                else None,
            )

            # Make the actual HTTP request
            # Note: self._http_client is passed in from the plugin, already instantiated
            response = await self._http_client.request(
                method=method,
                url=target_url,
                headers=headers,
                json=transformed_request,
                timeout=60.0,
            )

            # Get response body
            response_body = response.content

            # Transform response back if needed
            if response.status_code == 200 and "messages" in request_data:
                response_json = json.loads(response_body)
                adapted_json = self._response_transformer.transform_codex_to_chat(response_json)
                response_body = json.dumps(adapted_json).encode()

            return Response(
                content=response_body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.headers.get("content-type", "application/json"),
            )

        except httpx.HTTPError as e:
            self._logger.error("codex_adapter_http_error", error=str(e))
            return Response(
                content=json.dumps({"error": f"HTTP error: {str(e)}"}),
                status_code=500,
                media_type="application/json",
            )
        except Exception as e:
            self._logger.error("codex_adapter_request_error", error=str(e))
            return Response(
                content=json.dumps({"error": f"Request processing failed: {str(e)}"}),
                status_code=500,
                media_type="application/json",
            )

    async def handle_streaming(
        self, request: Request, endpoint: str, **kwargs: Any
    ) -> StreamingResponse:
        """Handle a streaming request.

        Makes actual streaming HTTP request to Codex API with proper transformation.
        """
        import json

        from ccproxy.auth.openai import OpenAITokenManager

        self._logger.debug(
            "codex_adapter_handle_streaming",
            endpoint=endpoint,
            has_kwargs=bool(kwargs),
        )

        # Get request body outside of generator
        body = await request.body()
        if body:
            request_data = json.loads(body)
        else:
            request_data = {}

        # Get session_id from kwargs or generate
        session_id = kwargs.get("session_id") or str(__import__("uuid").uuid4())

        # Get Codex instructions if available
        codex_instructions = None
        try:
            codex_instructions = await self._get_codex_instructions()
        except Exception as e:
            self._logger.warning(
                "codex_instructions_not_available_streaming",
                reason=str(e),
                error_type=type(e).__name__,
                msg="Proceeding without Codex instructions for streaming",
            )
        
        # Transform request using the request transformer
        is_openai_format = "messages" in request_data
        if is_openai_format:
            # Convert OpenAI format to Codex format
            transformed_request = self._request_transformer.transform_chat_to_codex(
                request_data,
                codex_instructions=codex_instructions,
            )
        else:
            # Native Codex format - just validate/enhance
            transformed_request = self._request_transformer.validate_codex_request(
                request_data,
                codex_instructions=codex_instructions,
            )

        # Build target URL - Codex API base URL
        # Session is passed in the request data, not in the URL
        base_url = "https://chatgpt.com"
        target_url = f"{base_url}/backend-api/codex/responses"

        self._logger.info(
            "codex_adapter_streaming_target_url",
            target_url=target_url,
            session_id=session_id,
            endpoint=endpoint,
        )

        # Prepare headers using transformer
        auth_headers = {}
        if self._auth_manager:
            self._logger.info(
                "codex_streaming_auth_manager_check",
                has_auth_manager=True,
                auth_manager_type=type(self._auth_manager).__name__,
                is_openai_manager=isinstance(self._auth_manager, OpenAITokenManager),
            )
            try:
                if isinstance(self._auth_manager, OpenAITokenManager):
                    auth_headers = await self._auth_manager.get_auth_headers()
                    self._logger.info(
                        "codex_streaming_auth_headers_added",
                        auth_header_keys=list(auth_headers.keys()),
                        has_authorization=("authorization" in auth_headers),
                    )
            except Exception as e:
                self._logger.error(
                    "codex_streaming_auth_headers_failed",
                    error=str(e),
                    error_type=type(e).__name__,
                )
        else:
            self._logger.warning(
                "codex_streaming_auth_manager_missing", msg="No auth manager set"
            )
        
        # Use transformer to prepare streaming headers
        headers = self._request_transformer.prepare_streaming_headers(
            dict(request.headers),
            session_id=session_id,
            auth_headers=auth_headers,
        )

        async def stream_generator() -> AsyncIterator[bytes]:
            try:
                # Log final headers for streaming (mask auth token)
                headers_to_log = dict(headers.items())
                if "authorization" in headers_to_log:
                    auth_val = headers_to_log["authorization"]
                    if auth_val.startswith("Bearer "):
                        headers_to_log["authorization"] = (
                            f"Bearer {auth_val[7:27]}..."
                            if len(auth_val) > 27
                            else "Bearer [SHORT]"
                        )

                self._logger.info(
                    "codex_streaming_request_headers",
                    headers=headers_to_log,
                    has_auth="authorization" in headers,
                    url=target_url,
                )

                # Log the request body being sent
                self._logger.info(
                    "codex_streaming_request_body",
                    has_instructions="instructions" in transformed_request,
                    has_input="input" in transformed_request,
                    model=transformed_request.get("model"),
                    stream=transformed_request.get("stream"),
                    body_keys=list(transformed_request.keys()),
                    body_preview=str(transformed_request)[:500]
                    if transformed_request
                    else None,
                )

                # Make the streaming HTTP request
                async with self._http_client.stream(
                    "POST",
                    target_url,
                    headers=headers,
                    json=transformed_request,
                    timeout=60.0,
                ) as response:
                    # Check for errors
                    if response.status_code >= 400:
                        error_body = await response.aread()
                        error_msg = {
                            "error": f"Codex API error: {response.status_code}",
                            "details": error_body.decode("utf-8", errors="ignore"),
                        }
                        yield f"data: {json.dumps(error_msg)}\n\n".encode()
                        yield b"data: [DONE]\n\n"
                        return

                    # Stream and transform response if needed
                    response_stream = response.aiter_bytes()
                    async for chunk in self._response_transformer.process_streaming_response(
                        response_stream,
                        transform_to_openai=is_openai_format,
                    ):
                        yield chunk

            except httpx.HTTPError as e:
                self._logger.error("codex_adapter_streaming_http_error", error=str(e))
                error_msg = {"error": f"HTTP streaming error: {str(e)}"}
                yield f"data: {json.dumps(error_msg)}\n\n".encode()
                yield b"data: [DONE]\n\n"
            except Exception as e:
                import traceback

                self._logger.error(
                    "codex_adapter_streaming_error",
                    error=str(e),
                    error_type=type(e).__name__,
                    traceback=traceback.format_exc(),
                )
                error_msg = {"error": f"Streaming failed: {str(e)}"}
                yield f"data: {json.dumps(error_msg)}\n\n".encode()
                yield b"data: [DONE]\n\n"

        return StreamingResponse(stream_generator(), media_type="text/event-stream")

    async def validate_request(
        self, request: Request, endpoint: str
    ) -> dict[str, Any] | None:
        """Validate request before processing.

        Args:
            request: FastAPI request object
            endpoint: Target endpoint path

        Returns:
            Validation result or None if valid
        """
        self._logger.debug(
            "codex_adapter_validate_request",
            endpoint=endpoint,
            method=request.method,
        )
        # Basic validation - could be extended
        return None

    async def transform_request(self, request_data: dict[str, Any]) -> dict[str, Any]:
        """Transform request data using the core adapter.

        Args:
            request_data: Original request data

        Returns:
            Transformed request data
        """
        self._logger.debug(
            "codex_adapter_transform_request",
            has_messages=bool(request_data.get("messages")),
            model=request_data.get("model"),
        )
        return await self._core_adapter.adapt_request(request_data)

    async def transform_response(self, response_data: dict[str, Any]) -> dict[str, Any]:
        """Transform response data using the core adapter.

        Args:
            response_data: Original response data

        Returns:
            Transformed response data
        """
        self._logger.debug(
            "codex_adapter_transform_response",
            response_id=response_data.get("id"),
            has_output=bool(response_data.get("output")),
        )
        return await self._core_adapter.adapt_response(response_data)

    def set_auth_manager(self, auth_manager: Any) -> None:
        """Set the authentication manager.

        Args:
            auth_manager: Authentication manager instance (e.g., OpenAITokenManager)
        """
        self._auth_manager = auth_manager

    def set_detection_service(self, detection_service: Any) -> None:
        """Set the detection service.

        Args:
            detection_service: Detection service instance (e.g., CodexDetectionService)
        """
        self._detection_service = detection_service

    async def _get_codex_instructions(self) -> str:
        """Get Codex CLI instructions from detection service.

        Returns:
            str: The Codex instructions

        Raises:
            PluginResourceError: If no instructions are available
        """
        if self._detection_service:
            try:
                # Import at runtime for isinstance check
                from .detection_service import CodexDetectionService

                # For actual CodexDetectionService, do isinstance check
                if isinstance(self._detection_service, CodexDetectionService):
                    # Get cached data (with automatic fallback handled internally)
                    cache_data = self._detection_service.get_cached_data()
                    if cache_data and cache_data.instructions:
                        self._logger.debug(
                            "Using Codex instructions from detection service"
                        )
                        return cache_data.instructions.instructions_field
            except ImportError:
                # If we can't import CodexDetectionService, try generic approach
                pass

            # Try generic approach for mock/test objects
            if hasattr(self._detection_service, "get_cached_data"):
                cache_data = self._detection_service.get_cached_data()
                if cache_data and hasattr(cache_data, "instructions"):
                    instructions = cache_data.instructions
                    if hasattr(instructions, "instructions_field"):
                        self._logger.debug(
                            "Using Codex instructions from detection service (generic)"
                        )
                        return instructions.instructions_field

        # No instructions found - raise exception
        from ccproxy.core.errors import PluginResourceError

        raise PluginResourceError(
            message="No Codex instructions available from detection service. "
            "Ensure Codex CLI is installed and detection has been initialized.",
            plugin_name="codex",
            resource_type="instructions",
        )

    async def cleanup(self) -> None:
        """Cleanup resources."""
        self._logger.debug("codex_adapter_cleanup")
        # Close our HTTP client
        if self._http_client:
            await self._http_client.aclose()

    # Additional methods that wrap the core adapter for compatibility

    async def adapt_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Convert a request from OpenAI to Codex format."""
        return await self._core_adapter.adapt_request(request)

    async def adapt_response(self, response: dict[str, Any]) -> dict[str, Any]:
        """Convert a response from Codex to OpenAI format."""
        return await self._core_adapter.adapt_response(response)

    async def adapt_stream(
        self, stream: AsyncIterator[dict[str, Any]]
    ) -> AsyncIterator[dict[str, Any]]:
        """Convert a streaming response from Codex to OpenAI format."""
        async for chunk in self._core_adapter.adapt_stream(stream):
            yield chunk

    def convert_chat_to_response_request(
        self, chat_request: dict[str, Any]
    ) -> dict[str, Any]:
        """Convert Chat Completions request to Response API format."""
        return self._core_adapter.adapt_chat_to_response_request(chat_request)

    def convert_response_to_chat(self, response_data: dict[str, Any]) -> dict[str, Any]:
        """Convert Response API response to Chat Completions format."""
        return self._core_adapter.adapt_response_to_chat(response_data)

    async def convert_response_stream_to_chat(
        self, response_stream: AsyncIterator[bytes]
    ) -> AsyncIterator[dict[str, Any]]:
        """Convert Response API SSE stream to Chat Completions format."""
        async for chunk in self._core_adapter.adapt_response_stream_to_chat(
            response_stream
        ):
            yield chunk

    def convert_error_to_chat_format(
        self, error_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Convert Response API error to Chat Completions error format."""
        # The OpenAI adapter doesn't have convert_error_to_chat_format, use adapt_error instead
        return self._core_adapter.adapt_error(error_data)

    # Provide access to the core adapter for direct use when needed
    def get_core_adapter(self) -> OpenAIAdapter:
        """Get the underlying core OpenAI adapter.

        This allows direct access to the core adapter when the plugin
        system needs to use it directly with ProviderContext.
        """
        return self._core_adapter
