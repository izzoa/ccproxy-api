import contextlib
import json
import uuid
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from ccproxy.core.constants import (
    FORMAT_OPENAI_CHAT,
    FORMAT_OPENAI_RESPONSES,
)
from ccproxy.core.logging import get_plugin_logger
from ccproxy.services.adapters.chain_composer import compose_from_chain
from ccproxy.services.adapters.http_adapter import BaseHTTPAdapter
from ccproxy.services.handler_config import HandlerConfig
from ccproxy.streaming import DeferredStreaming, StreamingBufferService
from ccproxy.utils.headers import (
    extract_request_headers,
    extract_response_headers,
    filter_request_headers,
    filter_response_headers,
)

from .detection_service import CodexDetectionService


logger = get_plugin_logger()


class CodexAdapter(BaseHTTPAdapter):
    """Simplified Codex adapter."""

    def __init__(
        self,
        detection_service: CodexDetectionService,
        config: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(config=config, **kwargs)
        self.detection_service = detection_service
        self.base_url = self.config.base_url.rstrip("/")

    async def handle_request(
        self, request: Request
    ) -> Response | StreamingResponse | DeferredStreaming:
        """Handle request with Codex-specific streaming behavior.

        Codex upstream only supports streaming. If the client requests a non-streaming
        response, we internally stream and buffer it, then return a standard Response.
        """
        # Context + request info
        ctx = request.state.context
        endpoint = ctx.metadata.get("endpoint", "")
        body = await request.body()
        headers = extract_request_headers(request)

        # Determine client streaming intent from body flag (fallback to False)
        wants_stream = False
        try:
            data = json.loads(body.decode()) if body else {}
            wants_stream = bool(data.get("stream", False))
        except Exception:  # Malformed/missing JSON -> assume non-streaming
            wants_stream = False
        logger.trace(
            "codex_adapter_request_intent",
            wants_stream=wants_stream,
            endpoint=endpoint,
            format_chain=getattr(ctx, "format_chain", []),
            category="streaming",
        )

        # Explicitly set service_type for downstream helpers
        with contextlib.suppress(Exception):
            ctx.metadata.setdefault("service_type", "codex")

        # If client wants streaming, delegate to streaming handler directly
        if wants_stream and self.streaming_handler:
            logger.trace(
                "codex_adapter_delegating_streaming",
                endpoint=endpoint,
                category="streaming",
            )
            return await self.handle_streaming(request, endpoint)

        # Otherwise, buffer the upstream streaming response into a standard one
        if getattr(self.config, "buffer_non_streaming", True):
            # 1) Prepare provider request (adds auth, sets stream=true, etc.)
            # Apply request format conversion if specified
            if ctx.format_chain and len(ctx.format_chain) > 1:
                try:
                    request_payload = self._decode_json_body(
                        body, context="codex_request"
                    )
                    request_payload = await self._apply_format_chain(
                        data=request_payload,
                        format_chain=ctx.format_chain,
                        stage="request",
                    )
                    body = self._encode_json_body(request_payload)
                except Exception as e:
                    logger.error(
                        "codex_format_chain_request_failed",
                        error=str(e),
                        exc_info=e,
                        category="transform",
                    )
                    return JSONResponse(
                        status_code=400,
                        content={
                            "error": {
                                "type": "invalid_request_error",
                                "message": "Failed to convert request using format chain",
                                "details": str(e),
                            }
                        },
                    )

            prepared_body, prepared_headers = await self.prepare_provider_request(
                body, headers, endpoint
            )
            logger.trace(
                "codex_adapter_prepared_provider_request",
                header_keys=list(prepared_headers.keys()),
                body_size=len(prepared_body or b""),
                category="http",
            )

            # 2) Build handler config using composed adapter from format_chain (unified path)

            composed_adapter = (
                compose_from_chain(
                    registry=self.format_registry, chain=ctx.format_chain
                )
                if self.format_registry and ctx.format_chain
                else None
            )

            handler_config = HandlerConfig(
                supports_streaming=True,
                request_transformer=None,
                response_adapter=composed_adapter,
                format_context=None,
            )

            # 3) Use StreamingBufferService to convert upstream stream -> regular response
            target_url = await self.get_target_url(endpoint)
            # Try to use a client with base_url for better hook integration
            http_client = await self.http_pool_manager.get_client()
            hook_manager = (
                getattr(self.streaming_handler, "hook_manager", None)
                if self.streaming_handler
                else None
            )
            buffer_service = StreamingBufferService(
                http_client=http_client,
                request_tracer=None,
                hook_manager=hook_manager,
                http_pool_manager=self.http_pool_manager,
            )

            buffered_response = await buffer_service.handle_buffered_streaming_request(
                method=request.method,
                url=target_url,
                headers=prepared_headers,
                body=prepared_body,
                handler_config=handler_config,
                request_context=ctx,
                provider_name="codex",
            )
            logger.trace(
                "codex_adapter_buffered_response_ready",
                status_code=buffered_response.status_code,
                category="streaming",
            )

            # 4) Apply reverse format chain on buffered body if needed
            if ctx.format_chain and len(ctx.format_chain) > 1:
                from typing import Literal

                mode: Literal["error", "response"] = (
                    "error" if buffered_response.status_code >= 400 else "response"
                )
                try:
                    # Ensure body is bytes for _decode_json_body
                    body_bytes = (
                        buffered_response.body
                        if isinstance(buffered_response.body, bytes)
                        else bytes(buffered_response.body)
                    )
                    response_payload = self._decode_json_body(
                        body_bytes, context=f"codex_{mode}"
                    )
                    response_payload = await self._apply_format_chain(
                        data=response_payload,
                        format_chain=ctx.format_chain,
                        stage=mode,
                    )
                    converted_body = self._encode_json_body(response_payload)
                except Exception as e:
                    logger.error(
                        "codex_format_chain_response_failed",
                        error=str(e),
                        mode=mode,
                        exc_info=e,
                        category="transform",
                    )
                    return JSONResponse(
                        status_code=502,
                        content={
                            "error": {
                                "type": "server_error",
                                "message": "Failed to convert provider response using format chain",
                                "details": str(e),
                            }
                        },
                    )

                # Filter headers and rebuild response; middleware will normalize headers
                headers_out = filter_response_headers(dict(buffered_response.headers))
                return Response(
                    content=converted_body,
                    status_code=buffered_response.status_code,
                    headers=headers_out,
                    media_type="application/json",
                )

            # No conversion needed; return buffered response as-is
            return buffered_response

        # Fallback: no buffering requested, use base non-streaming flow
        return await super().handle_request(request)

    async def get_target_url(self, endpoint: str) -> str:
        # Old URL: https://chat.openai.com/backend-anon/responses (308 redirect)
        return f"{self.base_url}/responses"

    async def prepare_provider_request(
        self, body: bytes, headers: dict[str, str], endpoint: str
    ) -> tuple[bytes, dict[str, str]]:
        # Get auth credentials and profile
        auth_data = await self.auth_manager.load_credentials()
        if not auth_data:
            raise ValueError("No authentication credentials available")

        # Get profile to extract chatgpt_account_id
        profile = await self.auth_manager.get_profile_quick()
        chatgpt_account_id = profile.chatgpt_account_id if profile else None

        # Parse body (format conversion is now handled by format chain)
        body_data = json.loads(body.decode()) if body else {}

        # Inject instructions mandatory for being allow to
        # to used the Codex API endpoint
        # Fetch detected instructions from detection service
        instructions = self._get_instructions()

        # if instructions is alreay set we will prepend the mandatory one
        # TODO: verify that it's workin
        if "instructions" in body_data:
            instructions = instructions + "\n" + body_data["instructions"]

        body_data["instructions"] = instructions

        # Codex backend requires stream=true, always override
        body_data["stream"] = True
        body_data["store"] = False

        # Codex does not support max_output_tokens, remove if present
        if "max_output_tokens" in body_data:
            body_data.pop("max_output_tokens")
        # Codex does not support max_output_tokens, remove if present
        if "max_completion_tokens" in body_data:
            body_data.pop("max_completion_tokens")

        # Remove any prefixed metadata fields that shouldn't be sent to the API
        body_data = self._remove_metadata_fields(body_data)

        # Filter and add headers
        filtered_headers = filter_request_headers(headers, preserve_auth=False)
        # fmt: off
        base_headers = {
            "authorization": f"Bearer {auth_data.access_token}",
            "content-type": "application/json",

            "session_id": filtered_headers["session_id"]
            if "sessions_id" in filtered_headers
            else str(uuid.uuid4()),

            "conversation_id": filtered_headers["conversation_id"]
            if "conversation_id" in filtered_headers
            else str(uuid.uuid4()),
        }

        # Add chatgpt-account-id only if available
        if chatgpt_account_id is not None:
            base_headers["chatgpt-account-id"] = chatgpt_account_id

        filtered_headers.update(base_headers)

        # Add CLI headers (skip empty redacted values, ignored keys, and redacted headers)
        if self.detection_service:
            cached_data = self.detection_service.get_cached_data()
            if cached_data and cached_data.headers:
                cli_headers: dict[str, str] = cached_data.headers
                ignores = set(
                    getattr(self.detection_service, "ignores_header", []) or []
                )
                redacted = set(getattr(self.detection_service, "REDACTED_HEADERS", []))
                for key, value in cli_headers.items():
                    lk = key.lower()
                    if lk in ignores or lk in redacted:
                        continue
                    if value is None or value == "":
                        continue
                    filtered_headers[lk] = value

        return json.dumps(body_data).encode(), filtered_headers

    async def process_provider_response(
        self, response: httpx.Response, endpoint: str
    ) -> Response | StreamingResponse:
        """Return a plain Response; streaming handled upstream by BaseHTTPAdapter.

        The BaseHTTPAdapter is responsible for detecting streaming and delegating
        to the shared StreamingHandler. For non-streaming responses, adapters
        should return a simple Starlette Response.
        """
        response_headers = extract_response_headers(response)
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=response_headers,
            media_type=response.headers.get("content-type"),
        )

    async def _create_streaming_response(
        self, response: httpx.Response, endpoint: str
    ) -> DeferredStreaming:
        """Create streaming response with format conversion support."""
        # Deprecated: streaming is centrally handled by BaseHTTPAdapter/StreamingHandler
        # Kept for compatibility; not used.
        raise NotImplementedError

    def _needs_format_conversion(self, endpoint: str) -> bool:
        """Deprecated: format conversion handled via format chain in BaseHTTPAdapter."""
        return False

    def _get_response_format_conversion(self, endpoint: str) -> tuple[str, str]:
        """Deprecated: conversion direction decided by format chain upstream."""
        return (FORMAT_OPENAI_RESPONSES, FORMAT_OPENAI_CHAT)

    async def handle_streaming(
        self, request: Request, endpoint: str, **kwargs: Any
    ) -> StreamingResponse | DeferredStreaming:
        """Handle streaming with request conversion for Codex.

        Applies request format conversion (e.g., anthropic.messages -> openai.responses) before
        preparing the provider request, then delegates to StreamingHandler with
        a streaming response adapter for reverse conversion as needed.
        """
        if not self.streaming_handler:
            # Fallback to base behavior
            return await super().handle_streaming(request, endpoint, **kwargs)

        # Get context
        ctx = request.state.context

        # Extract body and headers
        body = await request.body()
        headers = extract_request_headers(request)

        # Apply request format conversion if a chain is defined
        if ctx.format_chain and len(ctx.format_chain) > 1:
            try:
                request_payload = self._decode_json_body(
                    body, context="codex_stream_request"
                )
                request_payload = await self._apply_format_chain(
                    data=request_payload,
                    format_chain=ctx.format_chain,
                    stage="request",
                )
                body = self._encode_json_body(request_payload)
            except Exception as e:
                logger.error(
                    "codex_format_chain_request_failed",
                    error=str(e),
                    exc_info=e,
                    category="transform",
                )
                # Convert error to streaming response

                error_content = {
                    "error": {
                        "type": "invalid_request_error",
                        "message": "Failed to convert request using format chain",
                        "details": str(e),
                    }
                }
                error_bytes = json.dumps(error_content).encode("utf-8")

                async def error_generator() -> (
                    Any
                ):  # AsyncGenerator[bytes, None] would be more specific
                    yield error_bytes

                return StreamingResponse(
                    content=error_generator(),
                    status_code=400,
                    media_type="application/json",
                )

        # Provider-specific preparation (adds auth, sets stream=true)
        prepared_body, prepared_headers = await self.prepare_provider_request(
            body, headers, endpoint
        )

        # Get format adapter for streaming reverse conversion
        streaming_format_adapter = None
        if ctx.format_chain and len(ctx.format_chain) > 1 and self.format_registry:
            from_format = ctx.format_chain[-1]
            to_format = ctx.format_chain[0]
            try:
                streaming_format_adapter = self.format_registry.get_if_exists(
                    from_format, to_format
                )
            except Exception:
                streaming_format_adapter = None

        handler_config = HandlerConfig(
            supports_streaming=True,
            request_transformer=None,
            response_adapter=streaming_format_adapter,
            format_context=None,
        )

        target_url = await self.get_target_url(endpoint)

        parsed_url = urlparse(target_url)
        base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"

        return await self.streaming_handler.handle_streaming_request(
            method=request.method,
            url=target_url,
            headers=prepared_headers,
            body=prepared_body,
            handler_config=handler_config,
            request_context=ctx,
            client=await self.http_pool_manager.get_client(base_url=base_url),
        )

    # Helper methods
    def _remove_metadata_fields(self, data: dict[str, Any]) -> dict[str, Any]:
        """Remove fields that start with '_' as they are internal metadata.

        Args:
            data: Dictionary that may contain metadata fields

        Returns:
            Cleaned dictionary without metadata fields
        """
        if not isinstance(data, dict):
            return data

        # Create a new dict without keys starting with '_'
        cleaned_data: dict[str, Any] = {}
        for key, value in data.items():
            if not key.startswith("_"):
                # Recursively clean nested dictionaries
                if isinstance(value, dict):
                    cleaned_data[key] = self._remove_metadata_fields(value)
                elif isinstance(value, list):
                    # Clean list items if they are dictionaries
                    cleaned_items: list[Any] = []
                    for item in value:
                        if isinstance(item, dict):
                            cleaned_items.append(self._remove_metadata_fields(item))
                        else:
                            cleaned_items.append(item)
                    cleaned_data[key] = cleaned_items
                else:
                    cleaned_data[key] = value

        return cleaned_data

    def _get_instructions(self) -> str:
        if self.detection_service:
            injection = (
                self.detection_service.get_system_prompt()
            )  # returns {"instructions": str} or {}
            if injection and isinstance(injection.get("instructions"), str):
                instructions: str = injection["instructions"]
                return instructions
        raise ValueError("No instructions available from detection service")

    def adapt_error(self, error_body: dict[str, Any]) -> dict[str, Any]:
        """Convert Codex error format to appropriate API error format.

        Args:
            error_body: Codex error response

        Returns:
            API-formatted error response
        """
        # Handle the specific "Stream must be set to true" error
        if isinstance(error_body, dict) and "detail" in error_body:
            detail = error_body["detail"]
            if "Stream must be set to true" in detail:
                # Convert to generic invalid request error
                return {
                    "error": {
                        "type": "invalid_request_error",
                        "message": "Invalid streaming parameter",
                    }
                }

        # Handle other error formats that might have "error" key
        if "error" in error_body:
            return error_body

        # Default: wrap non-standard errors
        return {
            "error": {
                "type": "internal_server_error",
                "message": "An error occurred processing the request",
            }
        }
