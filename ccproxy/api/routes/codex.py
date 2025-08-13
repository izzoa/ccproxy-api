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
    except Exception as e:
        logger.error("Failed to get OpenAI access token", error=str(e))
        raise HTTPException(
            status_code=401, detail="Failed to retrieve valid credentials"
        ) from e

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
        raise HTTPException(status_code=401, detail=str(e)) from e
    except ProxyError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        logger.error("Unexpected error in codex_responses", error=str(e))
        raise HTTPException(status_code=500, detail="Internal server error") from e


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
    except Exception as e:
        logger.error("Failed to get OpenAI access token", error=str(e))
        raise HTTPException(
            status_code=401, detail="Failed to retrieve valid credentials"
        ) from e

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
        raise HTTPException(status_code=401, detail=str(e)) from e
    except ProxyError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        logger.error("Unexpected error in codex_responses_with_session", error=str(e))
        raise HTTPException(status_code=500, detail="Internal server error") from e


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
    except Exception as e:
        logger.error("Failed to get OpenAI access token", error=str(e))
        raise HTTPException(
            status_code=401, detail="Failed to retrieve valid credentials"
        ) from e

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
            def __init__(self, original_request, new_body):
                self.method = original_request.method
                self.url = original_request.url
                self.headers = dict(original_request.headers)
                self.headers["content-length"] = str(len(new_body))
                self.state = original_request.state
                self._body = new_body

            async def body(self):
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
            async def stream_codex_response() -> AsyncIterator[bytes]:
                """Stream and convert Response API to Chat Completions format."""
                async with httpx.AsyncClient(timeout=240.0) as client:
                    async with client.stream(
                        method="POST",
                        url=transformed_request["url"],
                        headers=transformed_request["headers"],
                        content=transformed_request["body"],
                    ) as response:
                        # Check if we got a streaming response
                        content_type = response.headers.get("content-type", "")
                        transfer_encoding = response.headers.get("transfer-encoding", "")
                        
                        logger.debug(
                            "codex_chat_response_headers",
                            status_code=response.status_code,
                            content_type=content_type,
                            transfer_encoding=transfer_encoding,
                            headers=dict(response.headers),
                            url=str(response.url),
                        )
                        
                        # Check if this is a streaming response
                        # The backend may return chunked transfer encoding without content-type
                        is_streaming = (
                            "text/event-stream" in content_type or
                            (transfer_encoding == "chunked" and not content_type)
                        )
                        
                        if is_streaming:
                            logger.debug(
                                "codex_stream_conversion_started",
                                session_id=session_id,
                                request_id=getattr(request.state, "request_id", "unknown"),
                            )

                            chunk_count = 0
                            total_bytes = 0
                            stream_id = f"chatcmpl_{uuid.uuid4().hex[:29]}"
                            created = int(time.time())

                            # Process SSE events directly without buffering
                            line_count = 0
                            first_chunk_sent = False
                            thinking_block_active = False
                            current_thinking_signature = None
                            try:
                                async for line in response.aiter_lines():
                                    line_count += 1
                                    logger.debug(
                                        "codex_stream_line",
                                        line_number=line_count,
                                        line_preview=line[:100] if line else "(empty)",
                                    )
                                    
                                    # Skip empty lines
                                    if not line or line.strip() == "":
                                        continue
                                        
                                    if line.startswith("data:"):
                                        data_str = line[5:].strip()
                                        if data_str == "[DONE]":
                                            continue

                                        try:
                                            event_data = json.loads(data_str)
                                            event_type = event_data.get("type")

                                            # Send initial role message if this is the first chunk
                                            if not first_chunk_sent:
                                                # Send an initial chunk to indicate streaming has started
                                                initial_chunk = {
                                                    "id": stream_id,
                                                    "object": "chat.completion.chunk",
                                                    "created": created,
                                                    "model": "gpt-5",
                                                    "choices": [
                                                        {
                                                            "index": 0,
                                                            "delta": {"role": "assistant"},
                                                            "finish_reason": None,
                                                        }
                                                    ],
                                                }
                                                yield f"data: {json.dumps(initial_chunk)}\n\n".encode()
                                                first_chunk_sent = True
                                                chunk_count += 1
                                                
                                                logger.debug(
                                                    "codex_stream_initial_chunk_sent",
                                                    event_type=event_type,
                                                )

                                            # Handle thinking/reasoning blocks - stream them wrapped in XML
                                            if event_type in ["response.output_item.added", "response.content_part.added", "response.reasoning_summary_part.added"]:
                                                # Check if this is a thinking/reasoning block
                                                item = event_data.get("item", {})
                                                part_type = item.get("type")
                                                
                                                # Check for both regular thinking blocks and reasoning summary parts
                                                if part_type in ["thinking", "reasoning", "internal_monologue"] or event_type == "response.reasoning_summary_part.added":
                                                    # Only send opening tag if not already in a thinking block
                                                    if not thinking_block_active:
                                                        # Start streaming thinking block with opening XML tag
                                                        thinking_block_active = True
                                                        current_thinking_signature = item.get("signature", "")
                                                        
                                                        logger.debug(
                                                            "codex_thinking_block_started",
                                                            part_type=part_type,
                                                            signature=current_thinking_signature,
                                                            event_type=event_type,
                                                        )
                                                        
                                                        # Send opening reasoning tag (different from regular thinking)
                                                        openai_chunk = {
                                                            "id": stream_id,
                                                            "object": "chat.completion.chunk",
                                                            "created": created,
                                                            "model": "gpt-5",
                                                            "choices": [
                                                                {
                                                                    "index": 0,
                                                                    "delta": {
                                                                        "content": f'<reasoning signature="{current_thinking_signature}">'
                                                                    },
                                                                    "finish_reason": None,
                                                                }
                                                            ],
                                                        }
                                                        yield f"data: {json.dumps(openai_chunk)}\n\n".encode()
                                                        chunk_count += 1
                                                    else:
                                                        # We're seeing a new reasoning part while already in a block
                                                        # This happens when there are multiple reasoning parts
                                                        logger.debug(
                                                            "codex_additional_reasoning_part",
                                                            event_type=event_type,
                                                            already_active=thinking_block_active,
                                                        )
                                                    
                                            # Handle thinking/reasoning summary text deltas
                                            elif event_type == "response.reasoning_summary_text.delta" and thinking_block_active:
                                                # Stream thinking content directly from reasoning summary
                                                delta_text = event_data.get("delta", "")
                                                if delta_text:
                                                    openai_chunk = {
                                                        "id": stream_id,
                                                        "object": "chat.completion.chunk",
                                                        "created": created,
                                                        "model": "gpt-5",
                                                        "choices": [
                                                            {
                                                                "index": 0,
                                                                "delta": {"content": delta_text},
                                                                "finish_reason": None,
                                                            }
                                                        ],
                                                    }
                                                    yield f"data: {json.dumps(openai_chunk)}\n\n".encode()
                                                    chunk_count += 1
                                                    
                                            # Handle thinking content deltas
                                            elif event_type == "response.output_item.delta" and thinking_block_active:
                                                # Stream thinking content directly
                                                item = event_data.get("item", {})
                                                if item.get("type") in ["thinking", "reasoning", "internal_monologue"]:
                                                    thinking_content = item.get("content", "")
                                                    if thinking_content:
                                                        openai_chunk = {
                                                            "id": stream_id,
                                                            "object": "chat.completion.chunk",
                                                            "created": created,
                                                            "model": "gpt-5",
                                                            "choices": [
                                                                {
                                                                    "index": 0,
                                                                    "delta": {"content": thinking_content},
                                                                    "finish_reason": None,
                                                                }
                                                            ],
                                                        }
                                                        yield f"data: {json.dumps(openai_chunk)}\n\n".encode()
                                                        chunk_count += 1
                                                        
                                            # Handle thinking block completion
                                            elif event_type in ["response.output_item.done", "response.reasoning_summary_part.done", "response.reasoning_summary_text.done"] and thinking_block_active:
                                                # Close the thinking block
                                                item = event_data.get("item", {})
                                                # Close on any of these done events when a thinking block is active
                                                if thinking_block_active:
                                                    thinking_block_active = False
                                                    
                                                    # Send closing reasoning tag
                                                    openai_chunk = {
                                                        "id": stream_id,
                                                        "object": "chat.completion.chunk",
                                                        "created": created,
                                                        "model": "gpt-5",
                                                        "choices": [
                                                            {
                                                                "index": 0,
                                                                "delta": {"content": "</reasoning>\n"},
                                                                "finish_reason": None,
                                                            }
                                                        ],
                                                    }
                                                    yield f"data: {json.dumps(openai_chunk)}\n\n".encode()
                                                    chunk_count += 1
                                                    
                                                    logger.debug(
                                                        "codex_thinking_block_ended",
                                                        signature=current_thinking_signature,
                                                    )
                                                    current_thinking_signature = None
                                                    
                                            # Convert Response API events to OpenAI format
                                            elif event_type == "response.output_text.delta":
                                                # Direct text delta event (only if not in thinking block)
                                                if not thinking_block_active:
                                                    delta_content = event_data.get("delta", "")
                                                    if delta_content:
                                                        chunk_count += 1
                                                        openai_chunk = {
                                                            "id": stream_id,
                                                            "object": "chat.completion.chunk",
                                                            "created": created,
                                                            "model": event_data.get(
                                                                "model", "gpt-5"
                                                            ),
                                                            "choices": [
                                                                {
                                                                    "index": 0,
                                                                    "delta": {
                                                                        "content": delta_content
                                                                    },
                                                                    "finish_reason": None,
                                                                }
                                                            ],
                                                        }
                                                        chunk_data = f"data: {json.dumps(openai_chunk)}\n\n".encode()
                                                        total_bytes += len(chunk_data)

                                                        logger.debug(
                                                            "codex_stream_chunk_converted",
                                                            chunk_number=chunk_count,
                                                            chunk_size=len(chunk_data),
                                                            event_type=event_type,
                                                            content_length=len(delta_content),
                                                        )

                                                        yield chunk_data

                                            elif event_type == "response.output.delta":
                                                # Standard output delta with nested structure
                                                output = event_data.get("output", [])
                                                for output_item in output:
                                                    if output_item.get("type") == "message":
                                                        content_blocks = output_item.get(
                                                            "content", []
                                                        )
                                                        for block in content_blocks:
                                                            # Check if this is thinking content
                                                            if block.get("type") in ["thinking", "reasoning", "internal_monologue"] and thinking_block_active:
                                                                thinking_content = block.get("text", "")
                                                                if thinking_content:
                                                                    chunk_count += 1
                                                                    openai_chunk = {
                                                                        "id": stream_id,
                                                                        "object": "chat.completion.chunk",
                                                                        "created": created,
                                                                        "model": "gpt-5",
                                                                        "choices": [
                                                                            {
                                                                                "index": 0,
                                                                                "delta": {"content": thinking_content},
                                                                                "finish_reason": None,
                                                                            }
                                                                        ],
                                                                    }
                                                                    yield f"data: {json.dumps(openai_chunk)}\n\n".encode()
                                                            elif block.get("type") in [
                                                                "output_text",
                                                                "text",
                                                            ] and not thinking_block_active:
                                                                delta_content = block.get(
                                                                    "text", ""
                                                                )
                                                                if delta_content:
                                                                    chunk_count += 1
                                                                    openai_chunk = {
                                                                        "id": stream_id,
                                                                        "object": "chat.completion.chunk",
                                                                        "created": created,
                                                                        "model": event_data.get(
                                                                            "model", "gpt-5"
                                                                        ),
                                                                        "choices": [
                                                                            {
                                                                                "index": 0,
                                                                                "delta": {
                                                                                    "content": delta_content
                                                                                },
                                                                                "finish_reason": None,
                                                                            }
                                                                        ],
                                                                    }
                                                                    chunk_data = f"data: {json.dumps(openai_chunk)}\n\n".encode()
                                                                    total_bytes += len(
                                                                        chunk_data
                                                                    )

                                                                    logger.debug(
                                                                        "codex_stream_chunk_converted",
                                                                        chunk_number=chunk_count,
                                                                        chunk_size=len(
                                                                            chunk_data
                                                                        ),
                                                                        event_type=event_type,
                                                                        content_length=len(
                                                                            delta_content
                                                                        ),
                                                                    )

                                                                    yield chunk_data

                                            elif event_type == "response.completed":
                                                # Final chunk with usage info
                                                response_obj = event_data.get("response", {})
                                                usage = response_obj.get("usage")

                                                openai_chunk = {
                                                    "id": stream_id,
                                                    "object": "chat.completion.chunk",
                                                    "created": created,
                                                    "model": response_obj.get("model", "gpt-5"),
                                                    "choices": [
                                                        {
                                                            "index": 0,
                                                            "delta": {},
                                                            "finish_reason": "stop",
                                                        }
                                                    ],
                                                }

                                                if usage:
                                                    openai_chunk["usage"] = {
                                                        "prompt_tokens": usage.get(
                                                            "input_tokens", 0
                                                        ),
                                                        "completion_tokens": usage.get(
                                                            "output_tokens", 0
                                                        ),
                                                        "total_tokens": usage.get(
                                                            "total_tokens", 0
                                                        ),
                                                    }

                                                chunk_data = f"data: {json.dumps(openai_chunk)}\n\n".encode()
                                                yield chunk_data

                                                logger.debug(
                                                    "codex_stream_completed",
                                                    total_chunks=chunk_count,
                                                    total_bytes=total_bytes,
                                                )

                                        except json.JSONDecodeError as e:
                                            logger.debug(
                                                "codex_sse_parse_failed",
                                                data_preview=data_str[:100],
                                                error=str(e),
                                            )
                                            continue

                            except Exception as e:
                                logger.error(
                                    "codex_stream_error",
                                    error=str(e),
                                    line_count=line_count,
                                )
                                raise
                            
                            # Send final [DONE] message
                            logger.debug(
                                "codex_stream_sending_done",
                                total_chunks=chunk_count,
                                total_bytes=total_bytes,
                            )
                            yield b"data: [DONE]\n\n"
                        else:
                            # Backend didn't return streaming or returned unexpected format
                            # When using client.stream(), we need to collect the response differently
                            chunks = []
                            async for chunk in response.aiter_bytes():
                                chunks.append(chunk)
                            
                            response_body = b"".join(chunks)
                            
                            logger.debug(
                                "codex_chat_non_streaming_response",
                                body_length=len(response_body),
                                body_preview=response_body[:200].decode("utf-8", errors="replace") if response_body else "empty",
                            )
                            
                            if response_body:
                                # Check if it's actually SSE data that we missed
                                body_str = response_body.decode("utf-8")
                                if body_str.startswith("event:") or body_str.startswith("data:"):
                                    # It's SSE data, try to extract the final JSON
                                    logger.warning("Backend returned SSE data but content-type was not text/event-stream")
                                    lines = body_str.strip().split("\n")
                                    for line in reversed(lines):
                                        if line.startswith("data:") and not line.endswith("[DONE]"):
                                            try:
                                                json_str = line[5:].strip()
                                                response_data = json.loads(json_str)
                                                if "response" in response_data:
                                                    response_data = response_data["response"]
                                                # Convert to OpenAI format and yield as a single chunk
                                                openai_response = adapter.response_to_chat_completion(response_data)
                                                yield f"data: {openai_response.model_dump_json()}\n\n".encode()
                                                yield b"data: [DONE]\n\n"
                                                return
                                            except json.JSONDecodeError:
                                                continue
                                    # Couldn't parse SSE data - yield error as SSE event
                                    error_response = {
                                        "error": {
                                            "message": "Failed to parse SSE response data",
                                            "type": "invalid_response_error",
                                            "code": 502
                                        }
                                    }
                                    yield f"data: {json.dumps(error_response)}\n\n".encode()
                                    yield b"data: [DONE]\n\n"
                                    return
                                else:
                                    # Try to parse as regular JSON
                                    try:
                                        response_data = json.loads(body_str)
                                        # Convert to Chat Completions format and yield as single chunk
                                        openai_response = adapter.response_to_chat_completion(response_data)
                                        yield f"data: {openai_response.model_dump_json()}\n\n".encode()
                                        yield b"data: [DONE]\n\n"
                                        return
                                    except json.JSONDecodeError as e:
                                        logger.error(
                                            "Failed to parse non-streaming response",
                                            error=str(e),
                                            body_preview=body_str[:500]
                                        )
                                        error_response = {
                                            "error": {
                                                "message": "Invalid JSON response from backend",
                                                "type": "invalid_response_error",
                                                "code": 502
                                            }
                                        }
                                        yield f"data: {json.dumps(error_response)}\n\n".encode()
                                        yield b"data: [DONE]\n\n"
                                        return
                            else:
                                # Empty response - yield error
                                error_response = {
                                    "error": {
                                        "message": "Backend returned empty response",
                                        "type": "empty_response_error",
                                        "code": 502
                                    }
                                }
                                yield f"data: {json.dumps(error_response)}\n\n".encode()
                                yield b"data: [DONE]\n\n"
                                return

            # Return streaming response with proper headers
            return StreamingResponseWithLogging(
                content=stream_codex_response(),
                request_context=request_context,
                metrics=getattr(proxy_service, "metrics", None),
                status_code=200,
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                },
            )
        else:
            # Handle non-streaming request using the proxy service
            response = await proxy_service.handle_codex_request(
                method="POST",
                path="/responses",
                session_id=session_id,
                access_token=access_token,
                request=mock_request,
                settings=settings,
            )

            # Check if this is a streaming response (shouldn't happen for non-streaming requests)
            is_streaming_response = isinstance(response, StreamingResponse)

            if is_streaming_response and not openai_request.stream:
                # User requested non-streaming but backend returned streaming
                # Consume the stream and convert to non-streaming response
                accumulated_content = ""
                final_response = None

                async for chunk in response.body_iterator:  # type: ignore
                    # The Response API sends SSE events
                    lines = chunk.decode("utf-8").strip().split("\n")
                    for line in lines:
                        if line.startswith("data:") and "[DONE]" not in line:
                            data_str = line[5:].strip()
                            try:
                                event_data = json.loads(data_str)
                                # Look for the completed response
                                if event_data.get("type") == "response.completed":
                                    final_response = event_data
                            except json.JSONDecodeError:
                                continue

                if final_response:
                    # Convert to Chat Completions format
                    return adapter.response_to_chat_completion(final_response)
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
                                error_data = json.loads(error_body.decode("utf-8"))
                                # Log the actual error from backend
                                logger.error(
                                    "codex_backend_error",
                                    status_code=response.status_code,
                                    error_data=error_data,
                                )
                                # Pass through the error from backend
                                raise HTTPException(
                                    status_code=response.status_code,
                                    detail=error_data.get("error", {}).get(
                                        "message", "Request failed"
                                    ),
                                )
                            except (json.JSONDecodeError, UnicodeDecodeError):
                                logger.error(
                                    "codex_backend_error_parse_failed",
                                    status_code=response.status_code,
                                    body=error_body[:500].decode("utf-8", errors="replace"),
                                )
                                pass
                        raise HTTPException(
                            status_code=response.status_code, detail="Request failed"
                        )

                    # Read the response body for successful responses
                    response_body = response.body
                    if response_body:
                        try:
                            response_data = json.loads(response_body.decode("utf-8"))
                            # Convert Response API format to Chat Completions format
                            return adapter.response_to_chat_completion(response_data)
                        except (json.JSONDecodeError, UnicodeDecodeError) as e:
                            logger.error("Failed to parse Codex response", error=str(e))
                            raise HTTPException(
                                status_code=502, detail="Invalid response from Codex API"
                            ) from e

                # If we can't convert, return error
                raise HTTPException(
                    status_code=502, detail="Unable to process Codex response"
                )

    except HTTPException:
        raise
    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    except ProxyError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        logger.error("Unexpected error in codex_chat_completions", error=str(e))
        raise HTTPException(status_code=500, detail="Internal server error") from e


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
#         raise HTTPException(status_code=401, detail=str(e)) from e
#     except ProxyError as e:
#         logger.warning(f"Proxy error for path /{path}: {str(e)}")
#         raise HTTPException(status_code=502, detail=str(e)) from e
#     except Exception as e:
#         logger.error(f"Unexpected error testing path /{path}", error=str(e))
#         raise HTTPException(status_code=500, detail=f"Error testing path: {str(e)}") from e
