"""Codex-specific adapter for OpenAI Response API format conversion.

This adapter provides a simplified interface for converting between
OpenAI Chat Completions format and OpenAI Response API format used by Codex.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import structlog

from ccproxy.adapters.base import APIAdapter

from .adapter import OpenAIAdapter


logger = structlog.get_logger(__name__)


class CodexAdapter(APIAdapter):
    """Simplified adapter for Codex Response API format conversion."""

    def __init__(self) -> None:
        """Initialize the Codex adapter."""
        self.openai_adapter = OpenAIAdapter()

    async def adapt_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Convert a request from OpenAI to Codex format.

        Args:
            request: The OpenAI format request data

        Returns:
            The Codex format request data
        """
        return self.convert_chat_to_response_request(request)

    async def adapt_response(self, response: dict[str, Any]) -> dict[str, Any]:
        """Convert a response from Codex to OpenAI format.

        Args:
            response: The Codex format response data

        Returns:
            The OpenAI format response data
        """
        return self.convert_response_to_chat(response)

    async def adapt_stream(  # type: ignore[override,misc]
        self, stream: AsyncIterator[dict[str, Any]]
    ) -> AsyncIterator[dict[str, Any]]:
        """Convert a streaming response from Codex to OpenAI format.

        Args:
            stream: The Codex format streaming response (Response API SSE events as dicts)

        Yields:
            The OpenAI format streaming response chunks (Chat Completions deltas)
        """
        # Convert Response API streaming dict events to Chat Completions format
        # The stream contains already-parsed Response API events as dictionaries
        async for chunk in self._convert_response_stream_dicts_to_chat(stream):
            yield chunk

    def convert_chat_to_response_request(
        self, chat_request: dict[str, Any]
    ) -> dict[str, Any]:
        """Convert Chat Completions request to Response API format.

        Args:
            chat_request: OpenAI Chat Completions request

        Returns:
            Response API formatted request ready for Codex backend
        """
        logger.debug(
            "codex_adapter_converting_request",
            has_messages=bool(chat_request.get("messages")),
            model=chat_request.get("model"),
            stream=chat_request.get("stream"),
        )

        response_request = self.openai_adapter.adapt_chat_to_response_request(
            chat_request
        )

        logger.debug(
            "codex_adapter_request_converted",
            response_model=response_request.get("model"),
            response_stream=response_request.get("stream"),
            has_instructions=bool(response_request.get("instructions")),
        )

        return response_request

    def convert_response_to_chat(self, response_data: dict[str, Any]) -> dict[str, Any]:
        """Convert Response API response to Chat Completions format.

        Args:
            response_data: Response API response from Codex backend

        Returns:
            Chat Completions formatted response
        """
        logger.debug(
            "codex_adapter_converting_response",
            response_id=response_data.get("id"),
            response_model=response_data.get("model"),
            has_output=bool(response_data.get("output")),
        )

        chat_response = self.openai_adapter.adapt_response_to_chat(response_data)

        logger.debug(
            "codex_adapter_response_converted",
            chat_id=chat_response.get("id"),
            chat_model=chat_response.get("model"),
            has_content=bool(
                chat_response.get("choices", [{}])[0].get("message", {}).get("content")
            ),
        )

        return chat_response

    async def convert_response_stream_to_chat(
        self, response_stream: AsyncIterator[bytes]
    ) -> AsyncIterator[dict[str, Any]]:
        """Convert Response API SSE stream to Chat Completions format.

        This replaces the 500+ line inline streaming conversion logic
        with a clean adapter-based approach.

        Args:
            response_stream: Async iterator of SSE bytes from Codex Response API

        Yields:
            Chat Completions formatted streaming chunks
        """
        logger.debug("codex_adapter_stream_conversion_started")

        chunk_count = 0
        async for chunk in self.openai_adapter.adapt_response_stream_to_chat(
            response_stream
        ):
            chunk_count += 1

            # Log every 10th chunk to avoid spam
            if chunk_count % 10 == 0 or chunk_count == 1:
                logger.debug(
                    "codex_adapter_stream_chunk_converted",
                    chunk_number=chunk_count,
                    chunk_id=chunk.get("id"),
                    has_content=bool(
                        chunk.get("choices", [{}])[0].get("delta", {}).get("content")
                    ),
                    finish_reason=chunk.get("choices", [{}])[0].get("finish_reason"),
                )

            yield chunk

        logger.debug(
            "codex_adapter_stream_conversion_completed",
            total_chunks=chunk_count,
        )

    async def _convert_response_stream_dicts_to_chat(
        self, stream: AsyncIterator[dict[str, Any]]
    ) -> AsyncIterator[dict[str, Any]]:
        """Convert Response API streaming dict events to Chat Completions format.

        This method handles already-parsed Response API events (as dicts)
        and converts them to Chat Completions streaming chunks.

        Args:
            stream: Async iterator of Response API events as dicts

        Yields:
            Chat Completions formatted streaming chunks as dicts
        """
        import time
        import uuid

        stream_id = f"chatcmpl_{uuid.uuid4().hex[:29]}"
        created = int(time.time())
        accumulated_content = ""

        logger.debug("codex_adapter_dict_stream_started", stream_id=stream_id)
        event_count = 0

        async for event_dict in stream:
            event_count += 1

            # The event_dict should already be parsed from SSE
            # It contains the event data directly
            event_type = event_dict.get("type")

            logger.debug(
                "codex_adapter_processing_dict_event",
                event_type=event_type,
                event_number=event_count,
                event_keys=list(event_dict.keys()) if event_dict else [],
            )

            # Handle different Response API event types
            if event_type in ["response.output.delta", "response.output_text.delta"]:
                # Extract delta content
                delta_content = ""

                if event_type == "response.output_text.delta":
                    # Direct text delta event
                    delta_content = event_dict.get("delta", "")
                else:
                    # Standard output delta with nested structure
                    output = event_dict.get("output", [])
                    if output:
                        for output_item in output:
                            if output_item.get("type") == "message":
                                content_blocks = output_item.get("content", [])
                                for block in content_blocks:
                                    if block.get("type") in ["output_text", "text"]:
                                        delta_content += block.get("text", "")

                if delta_content:
                    accumulated_content += delta_content

                    logger.debug(
                        "codex_adapter_yielding_dict_delta",
                        content_length=len(delta_content),
                        accumulated_length=len(accumulated_content),
                    )

                    # Create Chat Completions streaming chunk
                    yield {
                        "id": stream_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": event_dict.get("model", "gpt-4"),
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": delta_content},
                                "finish_reason": None,
                            }
                        ],
                    }

            elif event_type == "response.completed":
                # Final chunk with usage info
                response = event_dict.get("response", {})
                usage = response.get("usage")

                logger.debug(
                    "codex_adapter_dict_stream_completed",
                    total_content_length=len(accumulated_content),
                    has_usage=usage is not None,
                )

                chunk_data = {
                    "id": stream_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": response.get("model", "gpt-4"),
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }

                # Add usage if available
                if usage:
                    from ccproxy.adapters.openai.models import OpenAIUsage

                    converted_usage = OpenAIUsage(
                        prompt_tokens=usage.get("input_tokens", 0),
                        completion_tokens=usage.get("output_tokens", 0),
                        total_tokens=usage.get("total_tokens", 0),
                    )
                    chunk_data["usage"] = converted_usage.model_dump()

                yield chunk_data

            elif event_type in ["response.created", "response.in_progress"]:
                # These events don't produce output in Chat Completions format
                # Just log them for debugging
                logger.debug(
                    "codex_adapter_skipping_event",
                    event_type=event_type,
                    event_number=event_count,
                )
                continue

            # Handle other potential Response API events
            elif event_type and "output" in event_type:
                # Log unexpected output events for debugging
                logger.debug(
                    "codex_adapter_unexpected_output_event",
                    event_type=event_type,
                    event_data=event_dict,
                )

        logger.debug(
            "codex_adapter_dict_stream_finished",
            stream_id=stream_id,
            total_events=event_count,
            final_content_length=len(accumulated_content),
        )

    def convert_error_to_chat_format(
        self, error_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Convert Response API error to Chat Completions error format.

        Args:
            error_data: Error response from Codex Response API

        Returns:
            Chat Completions formatted error response
        """
        # The OpenAI adapter's error handling can be reused for Response API errors
        # since both use similar OpenAI error structures
        return self.openai_adapter.adapt_error(error_data)


__all__ = ["CodexAdapter"]
