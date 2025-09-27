import contextlib
import json
import time
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any, Literal, cast

from pydantic import BaseModel

import ccproxy.core.logging
from ccproxy.llms.formatters.constants import (
    ANTHROPIC_TO_OPENAI_ERROR_TYPE,
    ANTHROPIC_TO_OPENAI_FINISH_REASON,
)
from ccproxy.llms.formatters.utils import (
    anthropic_usage_snapshot,
    strict_parse_tool_arguments,
)
from ccproxy.llms.models import anthropic as anthropic_models
from ccproxy.llms.models import openai as openai_models


logger = ccproxy.core.logging.get_logger(__name__)

FinishReason = Literal["stop", "length", "tool_calls"]

ResponseStreamEvent = (
    openai_models.ResponseCreatedEvent
    | openai_models.ResponseInProgressEvent
    | openai_models.ResponseCompletedEvent
    | openai_models.ResponseOutputTextDeltaEvent
    | openai_models.ResponseFunctionCallArgumentsDoneEvent
    | openai_models.ResponseRefusalDoneEvent
)


def convert__anthropic_usage_to_openai_completion__usage(
    usage: anthropic_models.Usage,
) -> openai_models.CompletionUsage:
    snapshot = anthropic_usage_snapshot(usage)

    cached_tokens = snapshot.cache_read_tokens or snapshot.cache_creation_tokens

    prompt_tokens_details = openai_models.PromptTokensDetails(
        cached_tokens=cached_tokens, audio_tokens=0
    )
    completion_tokens_details = openai_models.CompletionTokensDetails(
        reasoning_tokens=0,
        audio_tokens=0,
        accepted_prediction_tokens=0,
        rejected_prediction_tokens=0,
    )

    return openai_models.CompletionUsage(
        prompt_tokens=snapshot.input_tokens,
        completion_tokens=snapshot.output_tokens,
        total_tokens=snapshot.input_tokens + snapshot.output_tokens,
        prompt_tokens_details=prompt_tokens_details,
        completion_tokens_details=completion_tokens_details,
    )


def convert__anthropic_usage_to_openai_responses__usage(
    usage: anthropic_models.Usage,
) -> openai_models.ResponseUsage:
    snapshot = anthropic_usage_snapshot(usage)

    cached_tokens = snapshot.cache_read_tokens or snapshot.cache_creation_tokens

    input_tokens_details = openai_models.InputTokensDetails(cached_tokens=cached_tokens)
    output_tokens_details = openai_models.OutputTokensDetails(reasoning_tokens=0)

    return openai_models.ResponseUsage(
        input_tokens=snapshot.input_tokens,
        input_tokens_details=input_tokens_details,
        output_tokens=snapshot.output_tokens,
        output_tokens_details=output_tokens_details,
        total_tokens=snapshot.input_tokens + snapshot.output_tokens,
    )


# Error helpers


def convert__anthropic_to_openai__error(error: BaseModel) -> BaseModel:
    """Convert an Anthropic error payload to the OpenAI envelope."""
    from ccproxy.llms.models.anthropic import ErrorResponse as AnthropicErrorResponse
    from ccproxy.llms.models.openai import ErrorDetail
    from ccproxy.llms.models.openai import ErrorResponse as OpenAIErrorResponse

    if isinstance(error, AnthropicErrorResponse):
        anthropic_error = error.error
        error_message = anthropic_error.message
        anthropic_error_type = "api_error"
        if hasattr(anthropic_error, "type"):
            anthropic_error_type = anthropic_error.type

        openai_error_type = ANTHROPIC_TO_OPENAI_ERROR_TYPE.get(
            anthropic_error_type, "api_error"
        )

        return OpenAIErrorResponse(
            error=ErrorDetail(
                message=error_message,
                type=openai_error_type,
                code=None,
                param=None,
            )
        )

    if hasattr(error, "error") and hasattr(error.error, "message"):
        error_message = error.error.message
        return OpenAIErrorResponse(
            error=ErrorDetail(
                message=error_message,
                type="api_error",
                code=None,
                param=None,
            )
        )

    error_message = "Unknown error occurred"
    if hasattr(error, "message"):
        error_message = error.message
    elif hasattr(error, "model_dump"):
        error_dict = error.model_dump()
        if isinstance(error_dict, dict):
            error_message = error_dict.get("message", str(error_dict))

    return OpenAIErrorResponse(
        error=ErrorDetail(
            message=error_message,
            type="api_error",
            code=None,
            param=None,
        )
    )


async def convert__anthropic_message_to_openai_responses__stream(
    stream: AsyncIterator[anthropic_models.MessageStreamEvent],
) -> AsyncGenerator[ResponseStreamEvent, None]:
    item_id = "msg_stream"
    output_index = 0
    content_index = 0
    model_id = ""
    response_id = ""
    sequence_counter = 0
    usage_prompt = 0
    usage_completion = 0

    first_logged = False
    async for event in stream:
        if not first_logged:
            first_logged = True
            with contextlib.suppress(Exception):
                logger.bind(
                    category="formatter", converter="anthropic_to_responses_stream"
                ).debug("anthropic_stream_first_chunk", evt_type=event.type)

        if isinstance(event, anthropic_models.PingEvent):
            continue
        if isinstance(event, anthropic_models.ErrorEvent):
            continue

        if isinstance(event, anthropic_models.MessageStartEvent):
            sequence_counter += 1
            message = event.message
            model_id = message.model or ""
            response_id = message.id or ""
            yield openai_models.ResponseCreatedEvent(
                type="response.created",
                sequence_number=sequence_counter,
                response=openai_models.ResponseObject(
                    id=response_id,
                    object="response",
                    created_at=0,
                    status="in_progress",
                    model=model_id,
                    output=[],
                    parallel_tool_calls=False,
                ),
            )

            for block in message.content or []:
                if isinstance(block, anthropic_models.ThinkingBlock):
                    sequence_counter += 1
                    sig_attr = (
                        f' signature="{block.signature}"' if block.signature else ""
                    )
                    thinking_xml = f"<thinking{sig_attr}>{block.thinking}</thinking>"
                    yield openai_models.ResponseOutputTextDeltaEvent(
                        type="response.output_text.delta",
                        sequence_number=sequence_counter,
                        item_id=item_id,
                        output_index=output_index,
                        content_index=content_index,
                        delta=thinking_xml,
                    )
            continue

        if isinstance(event, anthropic_models.ContentBlockStartEvent):
            block = event.content_block
            if isinstance(block, anthropic_models.ToolUseBlock):
                sequence_counter += 1
                args_dict = strict_parse_tool_arguments(block.input)
                args_str = json.dumps(args_dict, separators=(",", ":"))
                yield openai_models.ResponseFunctionCallArgumentsDoneEvent(
                    type="response.function_call_arguments.done",
                    sequence_number=sequence_counter,
                    item_id=item_id,
                    output_index=output_index,
                    arguments=args_str,
                )
            continue

        if isinstance(event, anthropic_models.ContentBlockDeltaEvent):
            delta = event.delta
            text = None
            if isinstance(
                delta, anthropic_models.TextDelta | anthropic_models.TextBlock
            ):
                text = delta.text
            if text:
                sequence_counter += 1
                with contextlib.suppress(Exception):
                    logger.bind(
                        category="formatter", converter="anthropic_to_responses_stream"
                    ).debug("anthropic_delta_emitted", preview=text[:20])
                yield openai_models.ResponseOutputTextDeltaEvent(
                    type="response.output_text.delta",
                    sequence_number=sequence_counter,
                    item_id=item_id,
                    output_index=output_index,
                    content_index=content_index,
                    delta=text,
                )
            continue

        if isinstance(event, anthropic_models.MessageDeltaEvent):
            sequence_counter += 1
            usage_prompt = event.usage.input_tokens or 0
            usage_completion = event.usage.output_tokens or 0
            usage_model = convert__anthropic_usage_to_openai_responses__usage(
                event.usage
            )
            yield openai_models.ResponseInProgressEvent(
                type="response.in_progress",
                sequence_number=sequence_counter,
                response=openai_models.ResponseObject(
                    id=response_id,
                    object="response",
                    created_at=0,
                    status="in_progress",
                    model=model_id,
                    output=[],
                    parallel_tool_calls=False,
                    usage=usage_model,
                ),
            )
            stop_reason = event.delta.stop_reason
            if stop_reason == "refusal":
                sequence_counter += 1
                yield openai_models.ResponseRefusalDoneEvent(
                    type="response.refusal.done",
                    sequence_number=sequence_counter,
                    item_id=item_id,
                    output_index=output_index,
                    content_index=content_index,
                    refusal="refused",
                )
            continue

        if isinstance(event, anthropic_models.MessageStopEvent):
            sequence_counter += 1
            yield openai_models.ResponseCompletedEvent(
                type="response.completed",
                sequence_number=sequence_counter,
                response=openai_models.ResponseObject(
                    id=response_id,
                    object="response",
                    created_at=0,
                    status="completed",
                    model=model_id,
                    output=[],
                    parallel_tool_calls=False,
                ),
            )
            continue

        # ContentBlockStopEvent and other events do not produce output
        continue


def convert__anthropic_message_to_openai_responses__request(
    request: anthropic_models.CreateMessageRequest,
) -> openai_models.ResponseRequest:
    """Convert Anthropic CreateMessageRequest to OpenAI ResponseRequest using typed models."""
    # Build OpenAI Responses request payload
    payload_data: dict[str, Any] = {
        "model": request.model,
    }

    if request.max_tokens is not None:
        payload_data["max_output_tokens"] = int(request.max_tokens)
    if request.stream:
        payload_data["stream"] = True

    # Map system to instructions if present
    if request.system:
        if isinstance(request.system, str):
            payload_data["instructions"] = request.system
        else:
            payload_data["instructions"] = "".join(
                block.text for block in request.system
            )

    # Map last user message text to Responses input
    last_user_text: str | None = None
    for msg in reversed(request.messages):
        if msg.role == "user":
            if isinstance(msg.content, str):
                last_user_text = msg.content
            elif isinstance(msg.content, list):
                texts: list[str] = []
                for block in msg.content:
                    # Support raw dicts and models
                    if isinstance(block, dict):
                        if block.get("type") == "text" and isinstance(
                            block.get("text"), str
                        ):
                            texts.append(block.get("text") or "")
                    else:
                        # Type guard for TextBlock
                        if (
                            getattr(block, "type", None) == "text"
                            and hasattr(block, "text")
                            and isinstance(getattr(block, "text", None), str)
                        ):
                            texts.append(block.text or "")
                if texts:
                    last_user_text = " ".join(texts)
            break

    # Always provide an input field matching ResponseRequest schema
    if last_user_text:
        payload_data["input"] = [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": last_user_text},
                ],
            }
        ]
    else:
        # Provide an empty input list if no user text detected to satisfy schema
        payload_data["input"] = []

    # Tools mapping (custom tools -> function tools)
    if request.tools:
        tools: list[dict[str, Any]] = []
        for tool in request.tools:
            if isinstance(tool, anthropic_models.Tool):
                tools.append(
                    {
                        "type": "function",
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    }
                )
        if tools:
            payload_data["tools"] = tools

    # tool_choice mapping (+ parallel control)
    tc = request.tool_choice
    if tc is not None:
        tc_type = getattr(tc, "type", None)
        if tc_type == "none":
            payload_data["tool_choice"] = "none"
        elif tc_type == "auto":
            payload_data["tool_choice"] = "auto"
        elif tc_type == "any":
            payload_data["tool_choice"] = "required"
        elif tc_type == "tool":
            name = getattr(tc, "name", None)
            if name:
                payload_data["tool_choice"] = {
                    "type": "function",
                    "function": {"name": name},
                }
        disable_parallel = getattr(tc, "disable_parallel_tool_use", None)
        if isinstance(disable_parallel, bool):
            payload_data["parallel_tool_calls"] = not disable_parallel

    # Validate
    return openai_models.ResponseRequest.model_validate(payload_data)


def convert__anthropic_message_to_openai_chat__stream(
    stream: AsyncIterator[anthropic_models.MessageStreamEvent],
) -> AsyncGenerator[openai_models.ChatCompletionChunk, None]:
    """Convert Anthropic stream to OpenAI stream using typed models."""

    async def generator() -> AsyncGenerator[openai_models.ChatCompletionChunk, None]:
        model_id = ""
        finish_reason: FinishReason = "stop"
        usage_prompt = 0
        usage_completion = 0

        async for evt in stream:
            # Handle both dict and typed model inputs
            evt_type = None
            if isinstance(evt, dict):
                evt_type = evt.get("type")
                if not evt_type:
                    continue
            else:
                if not hasattr(evt, "type"):
                    continue
                evt_type = evt.type

            if evt_type == "message_start":
                if isinstance(evt, dict):
                    message = evt.get("message", {})
                    model_id = message.get("model", "") if message else ""
                else:
                    # Type guard: only MessageStartEvent has .message attribute
                    if hasattr(evt, "message"):
                        model_id = evt.message.model or ""
                    else:
                        model_id = ""
            elif evt_type == "content_block_start":
                # OpenAI doesn't have equivalent, but we can emit an empty delta to start the stream
                yield openai_models.ChatCompletionChunk(
                    id="chatcmpl-stream",
                    object="chat.completion.chunk",
                    created=0,
                    model=model_id,
                    choices=[
                        openai_models.StreamingChoice(
                            index=0,
                            delta=openai_models.DeltaMessage(
                                role="assistant", content=""
                            ),
                            finish_reason=None,
                        )
                    ],
                )
            elif evt_type == "content_block_delta":
                text = None
                if isinstance(evt, dict):
                    delta = evt.get("delta", {})
                    text = delta.get("text") if delta else None
                else:
                    # Type guard: only ContentBlockDeltaEvent has .delta attribute
                    text = None
                    if hasattr(evt, "delta") and evt.delta is not None:
                        # TextDelta has .text attribute, MessageDelta does not
                        text = getattr(evt.delta, "text", None)

                if text:
                    yield openai_models.ChatCompletionChunk(
                        id="chatcmpl-stream",
                        object="chat.completion.chunk",
                        created=0,
                        model=model_id,
                        choices=[
                            openai_models.StreamingChoice(
                                index=0,
                                delta=openai_models.DeltaMessage(
                                    role="assistant", content=text
                                ),
                                finish_reason=None,
                            )
                        ],
                    )
            elif evt_type == "message_delta":
                if isinstance(evt, dict):
                    delta = evt.get("delta", {})
                    stop_reason = delta.get("stop_reason") if delta else None
                    usage = evt.get("usage", {})
                    usage_prompt = usage.get("input_tokens", 0) if usage else 0
                    usage_completion = usage.get("output_tokens", 0) if usage else 0
                else:
                    # Type guard: only MessageDeltaEvent has .delta and .usage attributes
                    stop_reason = None
                    if hasattr(evt, "delta") and evt.delta is not None:
                        stop_reason = getattr(evt.delta, "stop_reason", None)

                    usage_prompt = 0
                    usage_completion = 0
                    if hasattr(evt, "usage") and evt.usage is not None:
                        usage_prompt = getattr(evt.usage, "input_tokens", 0)
                        usage_completion = getattr(evt.usage, "output_tokens", 0)

                if stop_reason:
                    finish_reason = cast(
                        FinishReason,
                        ANTHROPIC_TO_OPENAI_FINISH_REASON.get(stop_reason, "stop"),
                    )
            elif evt_type == "content_block_stop":
                # Content block has stopped, but we don't need to emit anything special for OpenAI
                pass
            elif evt_type == "ping":
                # Ping events don't need to be converted to OpenAI format
                pass
            elif evt_type == "message_stop":
                usage = None
                if usage_prompt or usage_completion:
                    usage = openai_models.CompletionUsage(
                        prompt_tokens=usage_prompt,
                        completion_tokens=usage_completion,
                        total_tokens=usage_prompt + usage_completion,
                    )
                yield openai_models.ChatCompletionChunk(
                    id="chatcmpl-stream",
                    object="chat.completion.chunk",
                    created=0,
                    model=model_id,
                    choices=[
                        openai_models.StreamingChoice(
                            index=0,
                            delta=openai_models.DeltaMessage(),
                            finish_reason=finish_reason,
                        )
                    ],
                    usage=usage,
                )
                break

    return generator()


def convert__anthropic_message_to_openai_responses__response(
    response: anthropic_models.MessageResponse,
) -> openai_models.ResponseObject:
    """Convert Anthropic MessageResponse to an OpenAI ResponseObject."""
    text_parts: list[str] = []
    tool_contents: list[dict[str, Any]] = []
    for block in response.content:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            text_parts.append(getattr(block, "text", ""))
        elif block_type == "thinking":
            thinking = getattr(block, "thinking", None) or ""
            signature = getattr(block, "signature", None)
            sig_attr = (
                f' signature="{signature}"'
                if isinstance(signature, str) and signature
                else ""
            )
            text_parts.append(f"<thinking{sig_attr}>{thinking}</thinking>")
        elif block_type == "tool_use":
            tool_contents.append(
                {
                    "type": "tool_use",
                    "id": getattr(block, "id", "tool_1"),
                    "name": getattr(block, "name", "function"),
                    "arguments": getattr(block, "input", {}) or {},
                }
            )

    message_content: list[dict[str, Any]] = []
    if text_parts:
        message_content.append(
            openai_models.OutputTextContent(
                type="output_text",
                text="".join(text_parts),
            ).model_dump()
        )
    message_content.extend(tool_contents)

    usage_model = None
    if response.usage is not None:
        usage_model = convert__anthropic_usage_to_openai_responses__usage(
            response.usage
        )

    return openai_models.ResponseObject(
        id=response.id,
        object="response",
        created_at=0,
        status="completed",
        model=response.model,
        output=[
            openai_models.MessageOutput(
                type="message",
                id=f"{response.id}_msg_0",
                status="completed",
                role="assistant",
                content=message_content,  # type: ignore[arg-type]
            )
        ],
        parallel_tool_calls=False,
        usage=usage_model,
    )


def convert__anthropic_message_to_openai_chat__request(
    request: anthropic_models.CreateMessageRequest,
) -> openai_models.ChatCompletionRequest:
    """Convert Anthropic CreateMessageRequest to OpenAI ChatCompletionRequest using typed models."""
    openai_messages: list[dict[str, Any]] = []
    # System prompt
    if request.system:
        if isinstance(request.system, str):
            sys_content = request.system
        else:
            sys_content = "".join(block.text for block in request.system)
        if sys_content:
            openai_messages.append({"role": "system", "content": sys_content})

    # User/assistant messages with text + data-url images
    for msg in request.messages:
        role = msg.role
        content = msg.content

        # Handle tool usage and results
        if role == "assistant" and isinstance(content, list):
            tool_calls = []
            text_parts = []
            for block in content:
                block_type = getattr(block, "type", None)
                if block_type == "tool_use":
                    # Type guard for ToolUseBlock
                    if hasattr(block, "id") and hasattr(block, "name"):
                        # Safely get input with fallback to empty dict
                        tool_input = getattr(block, "input", {}) or {}

                        # Ensure input is properly serialized as JSON
                        try:
                            args_str = json.dumps(tool_input)
                        except Exception:
                            args_str = json.dumps({"arguments": str(tool_input)})

                        tool_calls.append(
                            {
                                "id": block.id,
                                "type": "function",
                                "function": {
                                    "name": block.name,
                                    "arguments": args_str,
                                },
                            }
                        )
                elif block_type == "text":
                    # Type guard for TextBlock
                    if hasattr(block, "text"):
                        text_parts.append(block.text)
            if tool_calls:
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "tool_calls": tool_calls,
                }
                assistant_msg["content"] = " ".join(text_parts) if text_parts else None
                openai_messages.append(assistant_msg)
                continue
        elif role == "user" and isinstance(content, list):
            is_tool_result = any(
                getattr(b, "type", None) == "tool_result" for b in content
            )
            if is_tool_result:
                for block in content:
                    if getattr(block, "type", None) == "tool_result":
                        # Type guard for ToolResultBlock
                        if hasattr(block, "tool_use_id"):
                            # Get content with an empty string fallback
                            result_content = getattr(block, "content", "")

                            # Convert complex content to string representation
                            if not isinstance(result_content, str):
                                try:
                                    if isinstance(result_content, list):
                                        # Handle list of text blocks
                                        text_parts = []
                                        for part in result_content:
                                            if (
                                                hasattr(part, "text")
                                                and hasattr(part, "type")
                                                and part.type == "text"
                                            ):
                                                text_parts.append(part.text)
                                        if text_parts:
                                            result_content = " ".join(text_parts)
                                        else:
                                            result_content = json.dumps(result_content)
                                    else:
                                        # Convert other non-string content to JSON
                                        result_content = json.dumps(result_content)
                                except Exception:
                                    # Fallback to string representation
                                    result_content = str(result_content)

                            openai_messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": block.tool_use_id,
                                    "content": result_content,
                                }
                            )
                continue

        if isinstance(content, list):
            parts: list[dict[str, Any]] = []
            text_accum: list[str] = []
            for block in content:
                # Support both raw dicts and Anthropic model instances
                if isinstance(block, dict):
                    btype = block.get("type")
                    if btype == "text" and isinstance(block.get("text"), str):
                        text_accum.append(block.get("text") or "")
                    elif btype == "image":
                        source = block.get("source") or {}
                        if (
                            isinstance(source, dict)
                            and source.get("type") == "base64"
                            and isinstance(source.get("media_type"), str)
                            and isinstance(source.get("data"), str)
                        ):
                            url = f"data:{source['media_type']};base64,{source['data']}"
                            parts.append(
                                {
                                    "type": "image_url",
                                    "image_url": {"url": url},
                                }
                            )
                else:
                    # Pydantic models
                    btype = getattr(block, "type", None)
                    if (
                        btype == "text"
                        and hasattr(block, "text")
                        and isinstance(getattr(block, "text", None), str)
                    ):
                        text_accum.append(block.text or "")
                    elif btype == "image":
                        source = getattr(block, "source", None)
                        if (
                            source is not None
                            and getattr(source, "type", None) == "base64"
                            and isinstance(getattr(source, "media_type", None), str)
                            and isinstance(getattr(source, "data", None), str)
                        ):
                            url = f"data:{source.media_type};base64,{source.data}"
                            parts.append(
                                {
                                    "type": "image_url",
                                    "image_url": {"url": url},
                                }
                            )
            if parts or len(text_accum) > 1:
                if text_accum:
                    parts.insert(0, {"type": "text", "text": " ".join(text_accum)})
                openai_messages.append({"role": role, "content": parts})
            else:
                openai_messages.append(
                    {"role": role, "content": (text_accum[0] if text_accum else "")}
                )
        else:
            openai_messages.append({"role": role, "content": content})

    # Tools mapping (custom tools -> function tools)
    tools: list[dict[str, Any]] = []
    if request.tools:
        for tool in request.tools:
            if isinstance(tool, anthropic_models.Tool):
                tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": tool.input_schema,
                        },
                    }
                )

    params: dict[str, Any] = {
        "model": request.model,
        "messages": openai_messages,
        "max_completion_tokens": request.max_tokens,
        "stream": request.stream or None,
    }
    if tools:
        params["tools"] = tools

    # tool_choice mapping
    tc = request.tool_choice
    if tc is not None:
        tc_type = getattr(tc, "type", None)
        if tc_type == "none":
            params["tool_choice"] = "none"
        elif tc_type == "auto":
            params["tool_choice"] = "auto"
        elif tc_type == "any":
            params["tool_choice"] = "required"
        elif tc_type == "tool":
            name = getattr(tc, "name", None)
            if name:
                params["tool_choice"] = {
                    "type": "function",
                    "function": {"name": name},
                }
        # parallel_tool_calls from disable_parallel_tool_use
        disable_parallel = getattr(tc, "disable_parallel_tool_use", None)
        if isinstance(disable_parallel, bool):
            params["parallel_tool_calls"] = not disable_parallel

    # Validate against OpenAI model
    return openai_models.ChatCompletionRequest.model_validate(params)


def convert__anthropic_message_to_openai_chat__response(
    response: anthropic_models.MessageResponse,
) -> openai_models.ChatCompletionResponse:
    """Convert Anthropic MessageResponse to an OpenAI ChatCompletionResponse."""
    content_blocks = response.content
    parts: list[str] = []
    for block in content_blocks:
        btype = getattr(block, "type", None)
        if btype == "text":
            text = getattr(block, "text", None)
            if isinstance(text, str):
                parts.append(text)
        elif btype == "thinking":
            thinking = getattr(block, "thinking", None)
            signature = getattr(block, "signature", None)
            if isinstance(thinking, str):
                sig_attr = (
                    f' signature="{signature}"'
                    if isinstance(signature, str) and signature
                    else ""
                )
                parts.append(f"<thinking{sig_attr}>{thinking}</thinking>")

    content_text = "".join(parts)

    stop_reason = response.stop_reason
    finish_reason = ANTHROPIC_TO_OPENAI_FINISH_REASON.get(
        stop_reason or "end_turn", "stop"
    )

    usage_model = convert__anthropic_usage_to_openai_completion__usage(response.usage)

    payload = {
        "id": response.id,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content_text},
                "finish_reason": finish_reason,
            }
        ],
        "created": int(time.time()),
        "model": response.model,
        "object": "chat.completion",
        "usage": usage_model.model_dump(),
    }

    return openai_models.ChatCompletionResponse.model_validate(payload)
