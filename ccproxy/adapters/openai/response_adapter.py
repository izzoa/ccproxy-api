"""Adapter for converting between OpenAI Chat Completions and Response API formats.

This adapter handles bidirectional conversion between:
- OpenAI Chat Completions API (used by most OpenAI clients)
- OpenAI Response API (used by Codex/ChatGPT backend)
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import structlog

from ccproxy.adapters.openai.models import (
    OpenAIChatCompletionRequest,
    OpenAIChatCompletionResponse,
    OpenAIChoice,
    OpenAIResponseMessage,
    OpenAIUsage,
)
from ccproxy.adapters.openai.response_models import (
    ResponseCompleted,
    ResponseMessage,
    ResponseMessageContent,
    ResponseReasoning,
    ResponseRequest,
)


logger = structlog.get_logger(__name__)


class ResponseAdapter:
    """Adapter for OpenAI Response API format conversion."""

    def chat_to_response_request(
        self, chat_request: dict[str, Any] | OpenAIChatCompletionRequest
    ) -> ResponseRequest:
        """Convert Chat Completions request to Response API format.

        Args:
            chat_request: OpenAI Chat Completions request

        Returns:
            Response API formatted request
        """
        if isinstance(chat_request, OpenAIChatCompletionRequest):
            chat_dict = chat_request.model_dump()
        else:
            chat_dict = chat_request

        # Extract messages and convert to Response API format
        messages = chat_dict.get("messages", [])
        response_input = []
        instructions = None

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            # System messages become instructions
            if role == "system":
                instructions = content
                continue

            # Convert user/assistant messages to Response API format
            response_msg = ResponseMessage(
                type="message",
                id=None,
                role=role if role in ["user", "assistant"] else "user",
                content=[
                    ResponseMessageContent(
                        type="input_text" if role == "user" else "output_text",
                        text=content if isinstance(content, str) else str(content),
                    )
                ],
            )
            response_input.append(response_msg)

        # Leave instructions field unset to let codex_transformers inject them
        # The backend validates instructions and needs the full Codex ones
        instructions = None
        # Actually, we need to not include the field at all if it's None
        # Otherwise the backend complains "Instructions are required"

        # Map model (Codex uses gpt-5)
        model = chat_dict.get("model", "gpt-4")
        # For Codex, we typically use gpt-5
        response_model = (
            "gpt-5" if "codex" in model.lower() or "gpt-5" in model.lower() else model
        )

        # Build Response API request
        # Note: Response API always requires stream=true and store=false
        # Also, Response API doesn't support temperature and other OpenAI-specific parameters
        request = ResponseRequest(
            model=response_model,
            instructions=instructions,
            input=response_input,
            stream=True,  # Always use streaming for Response API
            tool_choice="auto",
            parallel_tool_calls=chat_dict.get("parallel_tool_calls", False),
            reasoning=ResponseReasoning(effort="medium", summary="auto"),
            store=False,  # Must be false for Response API
            # The following parameters are not supported by Response API:
            # temperature, max_output_tokens, top_p, frequency_penalty, presence_penalty
        )

        return request

    def response_to_chat_completion(
        self, response_data: dict[str, Any] | ResponseCompleted
    ) -> OpenAIChatCompletionResponse:
        """Convert Response API response to Chat Completions format.

        Args:
            response_data: Response API response

        Returns:
            Chat Completions formatted response
        """
        # Extract the actual response data
        response_dict: dict[str, Any]
        if isinstance(response_data, ResponseCompleted):
            # Convert Pydantic model to dict
            response_dict = response_data.response.model_dump()
        else:  # isinstance(response_data, dict)
            if "response" in response_data:
                response_dict = response_data["response"]
            else:
                response_dict = response_data

        # Extract content from Response API output
        content = ""
        output = response_dict.get("output", [])
        # Look for message type output (skip reasoning)
        for output_item in output:
            if output_item.get("type") == "message":
                output_content = output_item.get("content", [])
                for content_block in output_content:
                    if content_block.get("type") in ["output_text", "text"]:
                        content += content_block.get("text", "")

        # Build Chat Completions response
        usage_data = response_dict.get("usage")
        converted_usage = self._convert_usage(usage_data) if usage_data else None

        return OpenAIChatCompletionResponse(
            id=response_dict.get("id", f"resp_{uuid.uuid4().hex}"),
            object="chat.completion",
            created=response_dict.get("created_at", int(time.time())),
            model=response_dict.get("model", "gpt-5"),
            choices=[
                OpenAIChoice(
                    index=0,
                    message=OpenAIResponseMessage(
                        role="assistant", content=content or None
                    ),
                    finish_reason="stop",
                )
            ],
            usage=converted_usage,
            system_fingerprint=response_dict.get("safety_identifier"),
        )

    async def stream_response_to_chat(
        self, response_stream: AsyncIterator[bytes]
    ) -> AsyncIterator[dict[str, Any]]:
        """Convert Response API SSE stream to Chat Completions format.

        Args:
            response_stream: Async iterator of SSE bytes from Response API

        Yields:
            Chat Completions formatted streaming chunks
        """
        stream_id = f"chatcmpl_{uuid.uuid4().hex[:29]}"
        created = int(time.time())
        accumulated_content = ""
        buffer = ""

        logger.debug("response_adapter_stream_started", stream_id=stream_id)
        raw_chunk_count = 0
        event_count = 0

        async for chunk in response_stream:
            raw_chunk_count += 1
            chunk_size = len(chunk)
            logger.debug(
                "response_adapter_raw_chunk_received",
                chunk_number=raw_chunk_count,
                chunk_size=chunk_size,
                buffer_size_before=len(buffer),
            )

            # Add chunk to buffer
            buffer += chunk.decode("utf-8")

            # Process complete SSE events (separated by double newlines)
            while "\n\n" in buffer:
                event_str, buffer = buffer.split("\n\n", 1)
                event_count += 1

                # Parse the SSE event
                event_type = None
                event_data = None

                for line in event_str.strip().split("\n"):
                    if not line:
                        continue

                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        data_str = line[5:].strip()
                        if data_str == "[DONE]":
                            logger.debug(
                                "response_adapter_done_marker_found",
                                event_number=event_count,
                            )
                            continue
                        try:
                            event_data = json.loads(data_str)
                        except json.JSONDecodeError:
                            logger.debug(
                                "response_adapter_sse_parse_failed",
                                data_preview=data_str[:100],
                                event_number=event_count,
                            )
                            continue

                # Process complete events
                if event_type and event_data:
                    logger.debug(
                        "response_adapter_sse_event_parsed",
                        event_type=event_type,
                        event_number=event_count,
                        has_output="output" in str(event_data),
                    )
                    if event_type in [
                        "response.output.delta",
                        "response.output_text.delta",
                    ]:
                        # Extract delta content
                        delta_content = ""

                        # Handle different event structures
                        if event_type == "response.output_text.delta":
                            # Direct text delta event
                            delta_content = event_data.get("delta", "")
                        else:
                            # Standard output delta with nested structure
                            output = event_data.get("output", [])
                            if output:
                                for output_item in output:
                                    if output_item.get("type") == "message":
                                        content_blocks = output_item.get("content", [])
                                        for block in content_blocks:
                                            if block.get("type") in [
                                                "output_text",
                                                "text",
                                            ]:
                                                delta_content += block.get("text", "")

                        if delta_content:
                            accumulated_content += delta_content

                            logger.debug(
                                "response_adapter_yielding_content",
                                content_length=len(delta_content),
                                accumulated_length=len(accumulated_content),
                            )

                            # Create Chat Completions streaming chunk
                            yield {
                                "id": stream_id,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": event_data.get("model", "gpt-5"),
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
                        response = event_data.get("response", {})
                        usage = response.get("usage")

                        logger.debug(
                            "response_adapter_stream_completed",
                            total_content_length=len(accumulated_content),
                            has_usage=usage is not None,
                        )

                        chunk_data = {
                            "id": stream_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": response.get("model", "gpt-5"),
                            "choices": [
                                {"index": 0, "delta": {}, "finish_reason": "stop"}
                            ],
                        }

                        # Add usage if available
                        converted_usage = self._convert_usage(usage) if usage else None
                        if converted_usage:
                            chunk_data["usage"] = converted_usage.model_dump()

                        yield chunk_data

        logger.debug(
            "response_adapter_stream_finished",
            stream_id=stream_id,
            total_raw_chunks=raw_chunk_count,
            total_events=event_count,
            final_buffer_size=len(buffer),
        )

    def _convert_usage(
        self, response_usage: dict[str, Any] | None
    ) -> OpenAIUsage | None:
        """Convert Response API usage to Chat Completions format."""
        if not response_usage:
            return None

        return OpenAIUsage(
            prompt_tokens=response_usage.get("input_tokens", 0),
            completion_tokens=response_usage.get("output_tokens", 0),
            total_tokens=response_usage.get("total_tokens", 0),
        )

    def _get_default_codex_instructions(self) -> str:
        """Get default Codex CLI instructions."""
        return (
            "You are a coding agent running in the Codex CLI, a terminal-based coding assistant. "
            "Codex CLI is an open source project led by OpenAI. You are expected to be precise, safe, and helpful.\n\n"
            "Your capabilities:\n"
            "- Receive user prompts and other context provided by the harness, such as files in the workspace.\n"
            "- Communicate with the user by streaming thinking & responses, and by making & updating plans.\n"
            "- Emit function calls to run terminal commands and apply patches. Depending on how this specific run is configured, "
            "you can request that these function calls be escalated to the user for approval before running. "
            'More on this in the "Sandbox and approvals" section.\n\n'
            "Within this context, Codex refers to the open-source agentic coding interface "
            "(not the old Codex language model built by OpenAI)."
        )
