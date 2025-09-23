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
        """Convert Chat Completions request to Response API format (sync version).
        
        This is the sync version for backward compatibility.
        For dynamic model info, use chat_to_response_request_async() instead.

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
            content = msg.get("content")

            # Handle system and developer messages as instructions
            if role in ["system", "developer"]:
                instruction_text = self._extract_text_from_content(content)
                if instruction_text:
                    if instructions:
                        instructions += "\n" + instruction_text
                    else:
                        instructions = instruction_text
                continue

            # Handle tool messages
            if role == "tool":
                # Tool messages need to be part of user messages in Response API
                tool_result_content = ResponseMessageContent(
                    type="input_text",
                    text=f"[Tool Result {msg.get('tool_call_id', 'unknown')}]: {content}",
                )
                # Add to last user message or create new one
                if response_input and response_input[-1].role == "user":
                    response_input[-1].content.append(tool_result_content)
                else:
                    response_msg = ResponseMessage(
                        type="message",
                        id=None,
                        role="user",
                        content=[tool_result_content],
                    )
                    response_input.append(response_msg)
                continue

            # Convert user/assistant messages to Response API format
            response_content = self._convert_content_to_response_format(content, role)
            
            # Handle tool calls in assistant messages
            if role == "assistant" and msg.get("tool_calls"):
                # Add tool calls as content blocks
                for tool_call in msg.get("tool_calls", []):
                    func = tool_call.get("function", {})
                    tool_text = f"[Tool Call {tool_call.get('id', '')}]: {func.get('name', '')}({func.get('arguments', '{}')})"
                    response_content.append(
                        ResponseMessageContent(
                            type="output_text",
                            text=tool_text,
                        )
                    )
            
            if response_content:
                response_msg = ResponseMessage(
                    type="message",
                    id=None,
                    role=role if role in ["user", "assistant"] else "user",
                    content=response_content,
                )
                response_input.append(response_msg)

        # Leave instructions field unset to let codex_transformers inject them
        # The backend validates instructions and needs the full Codex ones
        instructions = None

        # Map model for Response API
        model = chat_dict.get("model", "gpt-4")
        response_model = self._map_to_response_api_model(model)

        # Handle response_format for JSON mode
        response_format = chat_dict.get("response_format")
        if response_format and instructions:
            format_type = response_format.get("type") if isinstance(response_format, dict) else None
            if format_type == "json_object":
                instructions += "\nYou must respond with valid JSON only."
            elif format_type == "json_schema" and response_format.get("json_schema"):
                instructions += f"\nYou must respond with valid JSON that conforms to this schema: {response_format.get('json_schema')}"

        # Handle reasoning/thinking parameters
        reasoning_effort = chat_dict.get("reasoning_effort", "medium")
        if reasoning_effort or model.startswith("o1") or model.startswith("o3"):
            # Map reasoning effort levels
            effort_map = {
                "low": "low",
                "medium": "medium",
                "high": "high",
            }
            effort = effort_map.get(reasoning_effort, "medium")
            reasoning = ResponseReasoning(effort=effort, summary="auto")
        else:
            reasoning = ResponseReasoning(effort="medium", summary="auto")

        # Handle tool_choice
        tool_choice = "auto"
        if chat_dict.get("tool_choice"):
            tc = chat_dict.get("tool_choice")
            if isinstance(tc, str):
                tool_choice = tc if tc in ["auto", "none", "required"] else "auto"
            elif isinstance(tc, dict) and tc.get("type") == "function":
                # Specific function - Response API doesn't support this directly
                # We'd need to pass tool name somehow, but Response API doesn't have that
                tool_choice = "required"

        # Build Response API request
        request_dict = {
            "model": response_model,
            "input": response_input,
            "stream": True,  # Always use streaming for Response API
            "tool_choice": tool_choice,
            "parallel_tool_calls": chat_dict.get("parallel_tool_calls", False),
            "reasoning": reasoning,
            "store": False,  # Must be false for Response API
        }
        
        # Only add instructions if not None (to let codex_transformers inject them)
        if instructions is not None:
            request_dict["instructions"] = instructions

        # Audit and log unsupported OpenAI parameters
        unsupported_params = []
        
        # Parameters that Response API explicitly does not support
        if chat_dict.get("temperature") is not None:
            unsupported_params.append(f"temperature={chat_dict.get('temperature')}")
        if chat_dict.get("top_p") is not None:
            unsupported_params.append(f"top_p={chat_dict.get('top_p')}")
        if chat_dict.get("frequency_penalty") is not None:
            unsupported_params.append(f"frequency_penalty={chat_dict.get('frequency_penalty')}")
        if chat_dict.get("presence_penalty") is not None:
            unsupported_params.append(f"presence_penalty={chat_dict.get('presence_penalty')}")
        if chat_dict.get("seed") is not None:
            unsupported_params.append(f"seed={chat_dict.get('seed')}")
        if chat_dict.get("logprobs"):
            unsupported_params.append(f"logprobs={chat_dict.get('logprobs')}")
        if chat_dict.get("top_logprobs"):
            unsupported_params.append(f"top_logprobs={chat_dict.get('top_logprobs')}")
        if chat_dict.get("n") and chat_dict.get("n") != 1:
            unsupported_params.append(f"n={chat_dict.get('n')} (only n=1 supported)")
        if chat_dict.get("stop"):
            unsupported_params.append(f"stop={chat_dict.get('stop')}")
        if chat_dict.get("user"):
            unsupported_params.append(f"user={chat_dict.get('user')}")
        if chat_dict.get("metadata"):
            unsupported_params.append(f"metadata={chat_dict.get('metadata')}")
        
        # Log warning if unsupported parameters were provided
        if unsupported_params:
            import structlog
            logger = structlog.get_logger(__name__)
            logger.warning(
                "response_adapter_unsupported_parameters",
                parameters=unsupported_params,
                note="These OpenAI parameters are not supported by Response API and will be ignored"
            )
        
        # Handle max_tokens parameter
        # Response API uses max_output_tokens but it's not in the ResponseRequest model
        # It's handled at the API level, so we'll just note it
        if chat_dict.get("max_tokens"):
            # This would need to be handled at a different layer
            # since ResponseRequest doesn't have max_output_tokens field
            pass
        
        request = ResponseRequest(**request_dict)
        return request

    async def chat_to_response_request_async(
        self, chat_request: dict[str, Any] | OpenAIChatCompletionRequest
    ) -> ResponseRequest:
        """Convert Chat Completions request to Response API format with dynamic model info.

        Args:
            chat_request: OpenAI Chat Completions request

        Returns:
            Response API formatted request

        Raises:
            ValueError: If the request format is invalid
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
            content = msg.get("content")

            # Handle system and developer messages as instructions
            if role in ["system", "developer"]:
                instruction_text = self._extract_text_from_content(content)
                if instruction_text:
                    if instructions:
                        instructions += "\n" + instruction_text
                    else:
                        instructions = instruction_text
                continue

            # Handle tool messages
            if role == "tool":
                # Tool messages need to be part of user messages in Response API
                tool_result_content = ResponseMessageContent(
                    type="input_text",
                    text=f"[Tool Result {msg.get('tool_call_id', 'unknown')}]: {content}",
                )
                # Add to last user message or create new one
                if response_input and response_input[-1].role == "user":
                    response_input[-1].content.append(tool_result_content)
                else:
                    response_msg = ResponseMessage(
                        type="message",
                        id=None,
                        role="user",
                        content=[tool_result_content],
                    )
                    response_input.append(response_msg)
                continue

            # Convert user/assistant messages to Response API format
            response_content = self._convert_content_to_response_format(content, role)
            
            # Handle tool calls in assistant messages
            if role == "assistant" and msg.get("tool_calls"):
                # Add tool calls as content blocks
                for tool_call in msg.get("tool_calls", []):
                    func = tool_call.get("function", {})
                    tool_text = f"[Tool Call {tool_call.get('id', '')}]: {func.get('name', '')}({func.get('arguments', '{}')})"
                    response_content.append(
                        ResponseMessageContent(
                            type="output_text",
                            text=tool_text,
                        )
                    )
            
            if response_content:
                response_msg = ResponseMessage(
                    type="message",
                    id=None,
                    role=role if role in ["user", "assistant"] else "user",
                    content=response_content,
                )
                response_input.append(response_msg)

        # Leave instructions field unset to let codex_transformers inject them
        # The backend validates instructions and needs the full Codex ones
        instructions = None

        # Map model for Response API
        model = chat_dict.get("model", "gpt-4")
        response_model = self._map_to_response_api_model(model)

        # Get dynamic model info for max_tokens if needed
        max_tokens = chat_dict.get("max_tokens")
        if max_tokens is None:
            # Try to get model-specific default for max_tokens
            try:
                from ccproxy.services.model_info_service import get_model_info_service
                model_info_service = get_model_info_service()
                # Use a default for Codex models since they may not be in the Claude model list
                # Response API models typically support 8192 tokens
                max_tokens = 8192
                
                # Try to get dynamic info if available
                try:
                    # Check if we have info for this specific model
                    max_tokens = await model_info_service.get_max_output_tokens(response_model)
                except Exception:
                    # Model not found in dynamic info, use default
                    pass
            except Exception as e:
                import structlog
                logger = structlog.get_logger(__name__)
                logger.warning(
                    "failed_to_get_dynamic_max_tokens",
                    model=response_model,
                    error=str(e),
                    fallback=8192,
                )
                max_tokens = 8192  # Conservative fallback

        # Handle response_format for JSON mode
        response_format = chat_dict.get("response_format")
        if response_format and instructions:
            format_type = response_format.get("type") if isinstance(response_format, dict) else None
            if format_type == "json_object":
                instructions += "\nYou must respond with valid JSON only."
            elif format_type == "json_schema" and response_format.get("json_schema"):
                instructions += f"\nYou must respond with valid JSON that conforms to this schema: {response_format.get('json_schema')}"

        # Handle reasoning/thinking parameters
        reasoning_effort = chat_dict.get("reasoning_effort", "medium")
        if reasoning_effort or model.startswith("o1") or model.startswith("o3"):
            # Map reasoning effort levels
            effort_map = {
                "low": "low",
                "medium": "medium",
                "high": "high",
            }
            effort = effort_map.get(reasoning_effort, "medium")
            reasoning = ResponseReasoning(effort=effort, summary="auto")
        else:
            reasoning = ResponseReasoning(effort="medium", summary="auto")

        # Handle tool_choice
        tool_choice = "auto"
        if chat_dict.get("tool_choice"):
            tc = chat_dict.get("tool_choice")
            if isinstance(tc, str):
                tool_choice = tc if tc in ["auto", "none", "required"] else "auto"
            elif isinstance(tc, dict) and tc.get("type") == "function":
                # Specific function - Response API doesn't support this directly
                # We'd need to pass tool name somehow, but Response API doesn't have that
                tool_choice = "required"

        # Build Response API request
        request_dict = {
            "model": response_model,
            "input": response_input,
            "stream": True,  # Always use streaming for Response API
            "tool_choice": tool_choice,
            "parallel_tool_calls": chat_dict.get("parallel_tool_calls", False),
            "reasoning": reasoning,
            "store": False,  # Must be false for Response API
        }
        
        # Only add instructions if not None (to let codex_transformers inject them)
        if instructions is not None:
            request_dict["instructions"] = instructions

        # Audit and log unsupported OpenAI parameters
        unsupported_params = []
        
        # Parameters that Response API explicitly does not support
        if chat_dict.get("temperature") is not None:
            unsupported_params.append(f"temperature={chat_dict.get('temperature')}")
        if chat_dict.get("top_p") is not None:
            unsupported_params.append(f"top_p={chat_dict.get('top_p')}")
        if chat_dict.get("frequency_penalty") is not None:
            unsupported_params.append(f"frequency_penalty={chat_dict.get('frequency_penalty')}")
        if chat_dict.get("presence_penalty") is not None:
            unsupported_params.append(f"presence_penalty={chat_dict.get('presence_penalty')}")
        if chat_dict.get("seed") is not None:
            unsupported_params.append(f"seed={chat_dict.get('seed')}")
        if chat_dict.get("logprobs"):
            unsupported_params.append(f"logprobs={chat_dict.get('logprobs')}")
        if chat_dict.get("top_logprobs"):
            unsupported_params.append(f"top_logprobs={chat_dict.get('top_logprobs')}")
        if chat_dict.get("n") and chat_dict.get("n") != 1:
            unsupported_params.append(f"n={chat_dict.get('n')} (only n=1 supported)")
        if chat_dict.get("stop"):
            unsupported_params.append(f"stop={chat_dict.get('stop')}")
        if chat_dict.get("user"):
            unsupported_params.append(f"user={chat_dict.get('user')}")
        if chat_dict.get("metadata"):
            unsupported_params.append(f"metadata={chat_dict.get('metadata')}")
        
        # Log warning if unsupported parameters were provided
        if unsupported_params:
            import structlog
            logger = structlog.get_logger(__name__)
            logger.warning(
                "response_adapter_unsupported_parameters",
                parameters=unsupported_params,
                note="These OpenAI parameters are not supported by Response API and will be ignored"
            )
        
        request = ResponseRequest(**request_dict)
        return request

    def _extract_text_from_content(
        self, content: str | list[Any] | None
    ) -> str:
        """Extract text content from various content formats."""
        if content is None:
            return ""
        
        if isinstance(content, str):
            return content
        
        # Handle list of content blocks
        text_parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif block.get("type") == "image_url":
                    # Add placeholder for images
                    url = block.get("image_url", {}).get("url", "")
                    text_parts.append(f"[Image: {url[:100]}...]")
            elif hasattr(block, "type"):
                if block.type == "text" and hasattr(block, "text"):
                    text_parts.append(block.text)
                elif block.type == "image_url":
                    url = getattr(block.image_url, "url", "") if hasattr(block, "image_url") else ""
                    text_parts.append(f"[Image: {url[:100]}...]")
        
        return " ".join(text_parts)

    def _convert_content_to_response_format(
        self, content: str | list[Any] | None, role: str
    ) -> list[ResponseMessageContent]:
        """Convert various content formats to Response API format."""
        if content is None:
            return []
        
        content_type = "input_text" if role == "user" else "output_text"
        
        if isinstance(content, str):
            return [ResponseMessageContent(type=content_type, text=content)]
        
        # Handle list of content blocks
        response_content = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    response_content.append(
                        ResponseMessageContent(
                            type=content_type,
                            text=block.get("text", ""),
                        )
                    )
                elif block.get("type") == "image_url":
                    # Images need to be converted to text placeholders
                    url = block.get("image_url", {}).get("url", "")
                    response_content.append(
                        ResponseMessageContent(
                            type=content_type,
                            text=f"[Image: {url[:100]}...]",
                        )
                    )
            elif hasattr(block, "type"):
                if block.type == "text" and hasattr(block, "text"):
                    response_content.append(
                        ResponseMessageContent(
                            type=content_type,
                            text=block.text,
                        )
                    )
                elif block.type == "image_url":
                    url = getattr(block.image_url, "url", "") if hasattr(block, "image_url") else ""
                    response_content.append(
                        ResponseMessageContent(
                            type=content_type,
                            text=f"[Image: {url[:100]}...]",
                        )
                    )
        
        return response_content

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

        # Extract content and tool calls from Response API output
        content = ""
        tool_calls = []
        reasoning_content = ""
        
        output = response_dict.get("output", [])
        for output_item in output:
            if output_item.get("type") == "message":
                output_content = output_item.get("content", [])
                for content_block in output_content:
                    if content_block.get("type") in ["output_text", "text"]:
                        content += content_block.get("text", "")
            elif output_item.get("type") == "tool_use":
                # Convert tool use to OpenAI format
                tool_calls.append({
                    "id": output_item.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": output_item.get("name", ""),
                        "arguments": json.dumps(output_item.get("input", {}))
                    }
                })
            elif output_item.get("type") == "reasoning":
                # Extract reasoning content
                reasoning_content = output_item.get("content", "")

        # Add reasoning to content if present
        if reasoning_content:
            content = f"<thinking>{reasoning_content}</thinking>\n{content}"

        # Determine finish reason
        stop_reason = response_dict.get("stop_reason", "end_turn")
        finish_reason = self._map_stop_reason(stop_reason, tool_calls)

        # Build Chat Completions response
        usage_data = response_dict.get("usage")
        converted_usage = self._convert_usage(usage_data) if usage_data else None

        message = OpenAIResponseMessage(
            role="assistant",
            content=content or None,
            tool_calls=tool_calls if tool_calls else None
        )

        return OpenAIChatCompletionResponse(
            id=response_dict.get("id", f"resp_{uuid.uuid4().hex}"),
            object="chat.completion",
            created=response_dict.get("created_at", int(time.time())),
            model=response_dict.get("model", "gpt-5"),
            choices=[
                OpenAIChoice(
                    index=0,
                    message=message,
                    finish_reason=finish_reason,
                )
            ],
            usage=converted_usage,
            system_fingerprint=response_dict.get("safety_identifier"),
        )

    def _map_stop_reason(
        self, stop_reason: str, tool_calls: list[dict[str, Any]]
    ) -> str:
        """Map Response API stop reason to OpenAI finish reason."""
        if tool_calls:
            return "tool_calls"
        
        mapping = {
            "end_turn": "stop",
            "max_tokens": "length",
            "stop_sequence": "stop",
            "tool_use": "tool_calls",
            "content_filter": "content_filter",
        }
        return mapping.get(stop_reason, "stop")

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
        accumulated_reasoning = ""
        accumulated_tool_calls = {}  # Track tool calls by ID
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
                    
                    # Handle text content deltas
                    if event_type in [
                        "response.output.delta",
                        "response.output_text.delta",
                    ]:
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
                                            if block.get("type") in ["output_text", "text"]:
                                                delta_content += block.get("text", "")

                        if delta_content:
                            accumulated_content += delta_content
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

                    # Handle reasoning/thinking deltas
                    elif event_type == "response.reasoning.delta":
                        reasoning_delta = event_data.get("delta", "")
                        if reasoning_delta:
                            accumulated_reasoning += reasoning_delta
                            # Emit reasoning as content with special markers
                            yield {
                                "id": stream_id,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": event_data.get("model", "gpt-5"),
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {
                                            "content": f"<thinking>{reasoning_delta}</thinking>"
                                        },
                                        "finish_reason": None,
                                    }
                                ],
                            }

                    # Handle tool use deltas
                    elif event_type == "response.tool_use.delta":
                        tool_id = event_data.get("id")
                        tool_name = event_data.get("name")
                        tool_input_delta = event_data.get("input_delta", "")
                        
                        if tool_id:
                            if tool_id not in accumulated_tool_calls:
                                # First delta for this tool call
                                accumulated_tool_calls[tool_id] = {
                                    "id": tool_id,
                                    "type": "function",
                                    "function": {
                                        "name": tool_name or "",
                                        "arguments": tool_input_delta,
                                    }
                                }
                                # Emit initial tool call
                                yield {
                                    "id": stream_id,
                                    "object": "chat.completion.chunk",
                                    "created": created,
                                    "model": event_data.get("model", "gpt-5"),
                                    "choices": [
                                        {
                                            "index": 0,
                                            "delta": {
                                                "tool_calls": [{
                                                    "index": len(accumulated_tool_calls) - 1,
                                                    "id": tool_id,
                                                    "type": "function",
                                                    "function": {
                                                        "name": tool_name,
                                                        "arguments": tool_input_delta,
                                                    }
                                                }]
                                            },
                                            "finish_reason": None,
                                        }
                                    ],
                                }
                            else:
                                # Additional delta for existing tool call
                                accumulated_tool_calls[tool_id]["function"]["arguments"] += tool_input_delta
                                # Emit tool call delta
                                tool_index = list(accumulated_tool_calls.keys()).index(tool_id)
                                yield {
                                    "id": stream_id,
                                    "object": "chat.completion.chunk",
                                    "created": created,
                                    "model": event_data.get("model", "gpt-5"),
                                    "choices": [
                                        {
                                            "index": 0,
                                            "delta": {
                                                "tool_calls": [{
                                                    "index": tool_index,
                                                    "function": {
                                                        "arguments": tool_input_delta,
                                                    }
                                                }]
                                            },
                                            "finish_reason": None,
                                        }
                                    ],
                                }

                    # Handle completion
                    elif event_type == "response.completed":
                        response = event_data.get("response", {})
                        usage = response.get("usage")
                        
                        # Determine finish reason
                        finish_reason = "stop"
                        if accumulated_tool_calls:
                            finish_reason = "tool_calls"
                        elif response.get("stop_reason") == "max_tokens":
                            finish_reason = "length"

                        chunk_data = {
                            "id": stream_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": response.get("model", "gpt-5"),
                            "choices": [
                                {"index": 0, "delta": {}, "finish_reason": finish_reason}
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
            total_content_length=len(accumulated_content),
            total_reasoning_length=len(accumulated_reasoning),
            total_tool_calls=len(accumulated_tool_calls),
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
