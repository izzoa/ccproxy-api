import contextlib
import json
import os
import re
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal

import ccproxy.core.logging
from ccproxy.llms.models import anthropic as anthropic_models
from ccproxy.llms.models import openai as openai_models


logger = ccproxy.core.logging.get_logger(__name__)


TOOL_FUNCTION_KEYS = {"name", "description", "parameters"}

_LAST_REQUEST_TOOLS: list[Any] | None = None


THINKING_PATTERN = re.compile(
    r"<thinking(?:\s+signature=\"([^\"]*)\")?>(.*?)</thinking>",
    re.DOTALL,
)
THINKING_OPEN_PATTERN = re.compile(
    r"<thinking(?:\s+signature=\"([^\"]*)\")?\s*>",
    re.IGNORECASE,
)
THINKING_CLOSE_PATTERN = re.compile(r"</thinking>", re.IGNORECASE)

_REASONING_SUMMARY_MODES = {"auto", "concise", "detailed"}


@dataclass(slots=True)
class ThinkingSegment:
    """Lightweight reasoning segment mirroring Anthropics ThinkingBlock."""

    thinking: str
    signature: str | None = None

    def to_block(self) -> anthropic_models.ThinkingBlock:
        return anthropic_models.ThinkingBlock(
            type="thinking",
            thinking=self.thinking,
            signature=self.signature or "",
        )

    def to_xml(self) -> str:
        block = self.to_block()
        signature = block.signature.strip()
        signature_attr = f' signature="{signature}"' if signature else ""
        return f"<thinking{signature_attr}>{block.thinking}</thinking>"

    @classmethod
    def from_xml(cls, signature: str | None, text: str) -> "ThinkingSegment":
        return cls(thinking=text, signature=signature or None)


def _get_attr(obj: Any, name: str) -> Any:
    """Safely fetch an attribute from either dicts or objects."""

    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _adopt_summary_entry(entry: Any) -> dict[str, Any] | None:
    """Conversion of arbitrary summary nodes to dicts."""

    if isinstance(entry, dict):
        return entry

    if hasattr(entry, "model_dump"):
        with contextlib.suppress(Exception):
            data = entry.model_dump(mode="json", exclude_none=True)
            if isinstance(data, dict):
                return data
        with contextlib.suppress(Exception):
            data = entry.model_dump()
            if isinstance(data, dict):
                return data

    if hasattr(entry, "__dict__"):
        dict_data: dict[str, Any] = {}
        for key in (
            "type",
            "text",
            "content",
            "signature",
            "summary",
            "value",
            "delta",
            "reasoning",
        ):
            if hasattr(entry, key):
                value = getattr(entry, key)
                if value is not None:
                    dict_data[key] = value
        if dict_data:
            return dict_data
    return None


def _merge_thinking_segments(segments: list[ThinkingSegment]) -> list[ThinkingSegment]:
    merged: list[ThinkingSegment] = []
    for segment in segments:
        text = segment.thinking if isinstance(segment.thinking, str) else None
        if not text:
            continue
        signature = segment.signature or None
        if merged and merged[-1].signature == signature:
            merged[-1] = ThinkingSegment(
                thinking=merged[-1].thinking + text,
                signature=signature,
            )
        else:
            merged.append(ThinkingSegment(thinking=text, signature=signature))
    return merged


def _collect_reasoning_segments(source: Any) -> list[ThinkingSegment]:
    if source is None:
        return []

    segments: list[ThinkingSegment] = []
    visited: set[int] = set()

    def _walk(node: Any, inherited_signature: str | None) -> None:
        if node is None:
            return

        # if isinstance(node, str):
        #     if node:
        #         normalized = node.strip().lower()
        #         if normalized and normalized not in _REASONING_SUMMARY_MODES:
        #             segments.append(
        #                 ThinkingSegment(thinking=node, signature=inherited_signature)
        #             )
        #     return

        if isinstance(node, bytes | bytearray):
            try:
                decoded = node.decode()
            except UnicodeDecodeError:
                return
            if decoded:
                segments.append(
                    ThinkingSegment(thinking=decoded, signature=inherited_signature)
                )
            return

        if isinstance(node, list | tuple | set):
            node_id = id(node)
            if node_id in visited:
                return
            visited.add(node_id)
            start_idx = len(segments)
            current_signature = inherited_signature
            for child in node:
                child_data = _adopt_summary_entry(child)
                child_type = (
                    child_data.get("type")
                    if isinstance(child_data, dict)
                    else _get_attr(child, "type")
                )
                if child_type == "signature":
                    candidate = None
                    if isinstance(child_data, dict):
                        candidate = child_data.get("text") or child_data.get(
                            "signature"
                        )
                    else:
                        candidate = _get_attr(child, "text") or _get_attr(
                            child, "signature"
                        )
                    if isinstance(candidate, str) and candidate:
                        current_signature = candidate
                        for idx in range(start_idx, len(segments)):
                            segments[idx] = ThinkingSegment(
                                thinking=segments[idx].thinking,
                                signature=current_signature,
                            )
                        start_idx = len(segments)
                _walk(child, current_signature)
            return

        if isinstance(node, dict) or hasattr(node, "__dict__"):
            current_node_id = id(node)
            if current_node_id in visited:
                return
            visited.add(current_node_id)

        data = _adopt_summary_entry(node)
        if data is None:
            text_attr = _get_attr(node, "text")
            signature_attr = _get_attr(node, "signature")
            type_attr = _get_attr(node, "type")
            next_signature = inherited_signature
            if isinstance(signature_attr, str) and signature_attr:
                next_signature = signature_attr
            if type_attr == "signature" and isinstance(text_attr, str) and text_attr:
                next_signature = text_attr
                text_attr = None
            if isinstance(text_attr, str) and text_attr:
                segments.append(
                    ThinkingSegment(thinking=text_attr, signature=next_signature)
                )

            for key in ("summary", "content"):
                nested = _get_attr(node, key)
                if isinstance(nested, list | tuple | set):
                    for child in nested:
                        _walk(child, next_signature)
                elif isinstance(nested, dict):
                    _walk(nested, next_signature)
            return

        node_type = data.get("type")
        text_value = data.get("text")
        signature_value = data.get("signature")
        content_value = data.get("content")
        summary_value = data.get("summary")
        reasoning_value = data.get("reasoning")

        next_signature = inherited_signature
        if isinstance(signature_value, str) and signature_value:
            next_signature = signature_value

        if node_type == "signature":
            if isinstance(text_value, str) and text_value:
                next_signature = text_value
            if isinstance(content_value, list | tuple | set):
                for child in content_value:
                    _walk(child, next_signature)
            return

        if node_type in {"summary_group", "group"}:
            if isinstance(content_value, list | tuple | set):
                start_idx = len(segments)
                current_signature = next_signature
                for child in content_value:
                    child_data = _adopt_summary_entry(child)
                    child_type = (
                        child_data.get("type")
                        if isinstance(child_data, dict)
                        else _get_attr(child, "type")
                    )
                    if child_type == "signature":
                        candidate = None
                        if isinstance(child_data, dict):
                            candidate = child_data.get("text") or child_data.get(
                                "signature"
                            )
                        else:
                            candidate = _get_attr(child, "text") or _get_attr(
                                child, "signature"
                            )
                        if isinstance(candidate, str) and candidate:
                            current_signature = candidate
                            for idx in range(start_idx, len(segments)):
                                segments[idx] = ThinkingSegment(
                                    thinking=segments[idx].thinking,
                                    signature=current_signature,
                                )
                            start_idx = len(segments)
                    _walk(child, current_signature)
            return

        emitted = False
        if node_type in {"summary_text", "text", "reasoning_text"}:
            if isinstance(text_value, str) and text_value:
                segments.append(
                    ThinkingSegment(thinking=text_value, signature=next_signature)
                )
                emitted = True
        elif (
            isinstance(text_value, str)
            and text_value
            and node_type not in {"signature"}
        ):
            segments.append(
                ThinkingSegment(thinking=text_value, signature=next_signature)
            )
            emitted = True

        value_value = data.get("value")
        if not emitted and isinstance(value_value, str) and value_value:
            segments.append(
                ThinkingSegment(thinking=value_value, signature=next_signature)
            )
            emitted = True

        if isinstance(summary_value, list | tuple | set):
            for child in summary_value:
                _walk(child, next_signature)
        elif isinstance(summary_value, dict):
            _walk(summary_value, next_signature)

        if isinstance(content_value, list | tuple | set):
            for child in content_value:
                _walk(child, next_signature)
        elif isinstance(content_value, dict):
            _walk(content_value, next_signature)

        if isinstance(reasoning_value, list | tuple | set | dict):
            _walk(reasoning_value, next_signature)

    _walk(source, None)
    return _merge_thinking_segments(segments)


def _wrap_thinking(signature: str | None, text: str) -> str:
    """Serialize a reasoning block into <thinking> XML."""
    return ThinkingSegment(thinking=text, signature=signature).to_xml()


def _extract_reasoning_blocks(payload: Any) -> list[ThinkingSegment]:
    """Extract reasoning blocks from a response output payload."""

    if not payload:
        return []

    summary = _get_attr(payload, "summary")
    segments = _collect_reasoning_segments(summary)
    if segments:
        return segments

    if isinstance(payload, list | tuple | set):
        segments = _collect_reasoning_segments(payload)
        if segments:
            return segments

    text_value = _get_attr(payload, "text")
    if isinstance(text_value, str) and text_value:
        return [ThinkingSegment(thinking=text_value)]

    if isinstance(payload, dict):
        raw = payload.get("reasoning")
        if raw:
            return _extract_reasoning_blocks(raw)

    return []


def _split_content_segments(content: str) -> list[tuple[str, Any]]:
    """Split mixed assistant content into ordered text vs thinking blocks."""

    if not content:
        return []

    segments: list[tuple[str, Any]] = []
    last_idx = 0
    for match in THINKING_PATTERN.finditer(content):
        start, end = match.span()
        if start > last_idx:
            text_segment = content[last_idx:start]
            if text_segment:
                segments.append(("text", text_segment))
        signature = match.group(1) or None
        thinking_text = match.group(2) or ""
        segments.append(
            ("thinking", ThinkingSegment.from_xml(signature, thinking_text))
        )
        last_idx = end

    if last_idx < len(content):
        tail = content[last_idx:]
        if tail:
            segments.append(("text", tail))

    if not segments:
        segments.append(("text", content))
    return segments


def register_request_tools(tools: list[Any] | None) -> None:
    """Record the most recent request tool definitions for streaming heuristics."""

    global _LAST_REQUEST_TOOLS
    if tools:
        _LAST_REQUEST_TOOLS = list(tools)
    else:
        _LAST_REQUEST_TOOLS = None


def get_last_request_tools() -> list[Any] | None:
    """Return the last recorded request tools, if any."""

    if _LAST_REQUEST_TOOLS is None:
        return None
    return list(_LAST_REQUEST_TOOLS)


def _convert_tool_choice_responses_to_chat(
    tool_choice: Any,
) -> Any:
    """Responses tool choice (flat) → Chat tool choice (nested)."""

    if isinstance(tool_choice, dict):
        choice_type = tool_choice.get("type")
        if choice_type != "function":
            return tool_choice

        function_block = tool_choice.get("function")
        if isinstance(function_block, dict) and function_block.get("name"):
            return tool_choice

        name = None
        if isinstance(function_block, dict):
            name = function_block.get("name")
        if not name:
            name = tool_choice.get("name")

        if not name:
            return tool_choice

        new_choice = {
            key: value for key, value in tool_choice.items() if key not in {"name"}
        }
        new_choice["function"] = {"name": name}
        return new_choice

    return tool_choice


def _convert_tool_choice_chat_to_responses(tool_choice: Any) -> Any:
    """Chat tool choice (nested) → Responses tool choice (flat)."""

    if isinstance(tool_choice, dict):
        choice_type = tool_choice.get("type")
        if choice_type != "function":
            return tool_choice

        function_block = tool_choice.get("function")
        if not isinstance(function_block, dict):
            return tool_choice

        name = function_block.get("name")
        if not name:
            return tool_choice

        new_choice = {
            key: value for key, value in tool_choice.items() if key not in {"function"}
        }
        new_choice["name"] = name
        return new_choice

    return tool_choice


def _coerce_tool_dict(tool: Any) -> dict[str, Any] | None:
    """Return a shallow dict representation for a tool model/dict."""

    if hasattr(tool, "model_dump"):
        try:
            result = tool.model_dump(mode="json", exclude_none=True)
            if isinstance(result, dict):
                return result
            return None
        except TypeError:
            # Fallback for model_dump signatures without mode/exclude flags
            result = tool.model_dump()
            if isinstance(result, dict):
                return result
            return None
    if isinstance(tool, dict):
        return dict(tool)
    return None


def _convert_tools_responses_to_chat(
    tools: list[Any] | None,
) -> list[dict[str, Any]] | None:
    """Ensure Responses-style tools conform to ChatCompletion schema."""

    if not tools:
        return None

    converted: list[dict[str, Any]] = []
    for tool in tools:
        tool_dict = _coerce_tool_dict(tool)
        if not tool_dict:
            continue

        tool_type = tool_dict.get("type")
        if tool_type != "function":
            converted.append(tool_dict)
            continue

        function_block = tool_dict.get("function")
        if not isinstance(function_block, dict):
            fn_payload = {
                key: value
                for key, value in tool_dict.items()
                if key in TOOL_FUNCTION_KEYS and value is not None
            }
        else:
            fn_payload = {
                key: value
                for key, value in function_block.items()
                if key in TOOL_FUNCTION_KEYS and value is not None
            }
            # fall back to top-level metadata when function block omits values
            for key in TOOL_FUNCTION_KEYS:
                if key not in fn_payload and tool_dict.get(key) is not None:
                    fn_payload[key] = tool_dict[key]

        if "parameters" not in fn_payload or fn_payload.get("parameters") is None:
            fn_payload["parameters"] = {}

        new_tool = {
            key: value
            for key, value in tool_dict.items()
            if key not in (*TOOL_FUNCTION_KEYS, "function")
        }
        new_tool["function"] = fn_payload
        converted.append(new_tool)

    return converted or None


def _convert_tools_chat_to_responses(
    tools: list[Any] | None,
) -> list[dict[str, Any]] | None:
    """Ensure ChatCompletion tools match Responses API expectations."""

    if not tools:
        return None

    converted: list[dict[str, Any]] = []
    for tool in tools:
        tool_dict = _coerce_tool_dict(tool)
        if not tool_dict:
            continue

        tool_type = tool_dict.get("type")
        if tool_type != "function":
            converted.append(tool_dict)
            continue

        function_block = tool_dict.get("function")
        if not isinstance(function_block, dict):
            converted.append(tool_dict)
            continue

        base_tool = {
            key: value for key, value in tool_dict.items() if key not in {"function"}
        }

        for key in TOOL_FUNCTION_KEYS:
            value = function_block.get(key)
            if value is not None:
                base_tool[key] = value

        if "parameters" not in base_tool:
            base_tool["parameters"] = {}

        converted.append(base_tool)

    return converted or None


def convert__openai_responses_usage_to_openai_completion__usage(
    usage: openai_models.ResponseUsage,
) -> openai_models.CompletionUsage:
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)

    cached_tokens = 0
    input_details = getattr(usage, "input_tokens_details", None)
    if input_details:
        cached_tokens = int(getattr(input_details, "cached_tokens", 0) or 0)

    reasoning_tokens = 0
    output_details = getattr(usage, "output_tokens_details", None)
    if output_details:
        reasoning_tokens = int(getattr(output_details, "reasoning_tokens", 0) or 0)

    prompt_tokens_details = openai_models.PromptTokensDetails(
        cached_tokens=cached_tokens, audio_tokens=0
    )
    completion_tokens_details = openai_models.CompletionTokensDetails(
        reasoning_tokens=reasoning_tokens,
        audio_tokens=0,
        accepted_prediction_tokens=0,
        rejected_prediction_tokens=0,
    )

    return openai_models.CompletionUsage(
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        prompt_tokens_details=prompt_tokens_details,
        completion_tokens_details=completion_tokens_details,
    )


def convert__openai_completion_usage_to_openai_responses__usage(
    usage: openai_models.CompletionUsage,
) -> openai_models.ResponseUsage:
    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)

    cached_tokens = 0
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    if prompt_details:
        cached_tokens = int(getattr(prompt_details, "cached_tokens", 0) or 0)

    reasoning_tokens = 0
    completion_details = getattr(usage, "completion_tokens_details", None)
    if completion_details:
        reasoning_tokens = int(getattr(completion_details, "reasoning_tokens", 0) or 0)

    input_tokens_details = openai_models.InputTokensDetails(cached_tokens=cached_tokens)
    output_tokens_details = openai_models.OutputTokensDetails(
        reasoning_tokens=reasoning_tokens
    )

    return openai_models.ResponseUsage(
        input_tokens=prompt_tokens,
        input_tokens_details=input_tokens_details,
        output_tokens=completion_tokens,
        output_tokens_details=output_tokens_details,
        total_tokens=prompt_tokens + completion_tokens,
    )


async def convert__openai_responses_to_openaichat__request(
    request: openai_models.ResponseRequest,
) -> openai_models.ChatCompletionRequest:
    _log = logger.bind(category="formatter", converter="responses_to_chat_request")
    system_message: str | None = request.instructions
    messages: list[dict[str, Any]] = []

    # Handle string input shortcut
    if isinstance(request.input, str):
        messages.append({"role": "user", "content": request.input})
    else:
        for item in request.input or []:
            role = getattr(item, "role", None) or "user"
            content_blocks = getattr(item, "content", [])
            text_parts: list[str] = []

            for part in content_blocks or []:
                if isinstance(part, dict):
                    if part.get("type") in {"input_text", "text"}:
                        text = part.get("text")
                        if isinstance(text, str):
                            text_parts.append(text)
                else:
                    part_type = getattr(part, "type", None)
                    if part_type in {"input_text", "text"} and hasattr(part, "text"):
                        text_value = part.text
                        if isinstance(text_value, str):
                            text_parts.append(text_value)

            content_text = " ".join([p for p in text_parts if p]).strip()

            if not content_text:
                # Fallback to serialized content blocks if no plain text extracted
                blocks = []
                for part in content_blocks or []:
                    if isinstance(part, dict):
                        blocks.append(part)
                    elif hasattr(part, "model_dump"):
                        blocks.append(part.model_dump(mode="json"))
                if blocks:
                    content_text = json.dumps(blocks)

            if role == "system":
                # Merge all system content into a single system message
                system_message = content_text or system_message
            else:
                messages.append(
                    {
                        "role": role,
                        "content": content_text or "(empty request)",
                    }
                )

    if system_message:
        messages.insert(0, {"role": "system", "content": system_message})

    # Provide a default user prompt if none extracted
    if not messages:
        messages.append({"role": "user", "content": "(empty request)"})

    # Ensure all message contents are non-empty strings
    for entry in messages:
        content = entry.get("content")
        if not isinstance(content, str) or not content.strip():
            entry["content"] = (
                content.strip()
                if isinstance(content, str) and content.strip()
                else "(empty request)"
            )

    payload: dict[str, Any] = {
        "model": request.model or "gpt-4o-mini",
        "messages": messages,
    }

    reasoning_cfg = getattr(request, "reasoning", None)
    effort_value: Any = None
    if isinstance(reasoning_cfg, dict):
        effort_value = reasoning_cfg.get("effort")
    elif reasoning_cfg is not None:
        effort_value = _get_attr(reasoning_cfg, "effort")
    if isinstance(effort_value, str) and effort_value:
        payload["reasoning_effort"] = effort_value

    with contextlib.suppress(Exception):
        _log.debug(
            "responses_to_chat_compiled_messages",
            message_count=len(messages),
            roles=[m.get("role") for m in messages],
        )

    if request.max_output_tokens is not None:
        payload["max_completion_tokens"] = request.max_output_tokens

    if request.stream is not None:
        payload["stream"] = request.stream

    if request.temperature is not None:
        payload["temperature"] = request.temperature

    if request.top_p is not None:
        payload["top_p"] = request.top_p

    tools = _convert_tools_responses_to_chat(request.tools)
    if tools:
        payload["tools"] = tools

    if request.tool_choice is not None:
        payload["tool_choice"] = _convert_tool_choice_responses_to_chat(
            request.tool_choice
        )

    if request.parallel_tool_calls is not None:
        payload["parallel_tool_calls"] = request.parallel_tool_calls

    return openai_models.ChatCompletionRequest.model_validate(payload)


async def convert__openai_chat_to_openai_responses__response(
    chat_response: openai_models.ChatCompletionResponse,
) -> openai_models.ResponseObject:
    content_text = ""
    tool_calls: list[Any] = []
    if chat_response.choices:
        first_choice = chat_response.choices[0]
        if first_choice.message:
            content = first_choice.message.content
            if content:
                if isinstance(content, str):
                    content_text = content
                elif isinstance(content, list):
                    # Handle list content - convert to string
                    content_text = str(content)
                else:
                    content_text = str(content)
            if first_choice.message.tool_calls:
                tool_calls = list(first_choice.message.tool_calls)

    segments = _split_content_segments(content_text)

    outputs: list[Any] = []
    reasoning_entries: list[Any] = []
    message_buffer: list[str] = []
    message_counter = 0

    def flush_message() -> None:
        nonlocal message_buffer, message_counter
        if not message_buffer:
            return
        message_text = "".join(message_buffer)
        message_buffer = []
        message_id = f"msg_{chat_response.id or 'unknown'}_{message_counter}"
        message_counter += 1
        outputs.append(
            openai_models.MessageOutput(
                type="message",
                role="assistant",
                id=message_id,
                status="completed",
                content=[
                    openai_models.OutputTextContent(
                        type="output_text", text=message_text
                    )
                ],
            )
        )

    for segment in segments or [("text", "")]:
        if not segment:
            continue
        kind = segment[0]
        if kind == "text":
            text_part = segment[1]
            if isinstance(text_part, str) and text_part:
                message_buffer.append(text_part)
        elif kind == "thinking":
            segment_value = segment[1]
            if isinstance(segment_value, ThinkingSegment):
                signature = segment_value.signature
                thinking_text = segment_value.thinking
            else:
                signature = None
                thinking_text = ""
            flush_message()
            summary_entry: dict[str, Any] = {
                "type": "summary_text",
                "text": thinking_text,
            }
            if signature:
                summary_entry["signature"] = signature
            reasoning_id = (
                f"reasoning_{chat_response.id or 'unknown'}_{len(reasoning_entries)}"
            )
            reasoning_output = openai_models.ReasoningOutput(
                type="reasoning",
                id=reasoning_id,
                status="completed",
                summary=[summary_entry],
            )
            outputs.append(reasoning_output)
            reasoning_entries.append(reasoning_output)

    # Flush any remaining assistant text
    flush_message()

    if not outputs:
        outputs.append(
            openai_models.MessageOutput(
                type="message",
                role="assistant",
                id=f"msg_{chat_response.id or 'unknown'}_0",
                status="completed",
                content=[openai_models.OutputTextContent(type="output_text", text="")],
            )
        )

    if tool_calls:
        for idx, tool_call in enumerate(tool_calls):
            fn = getattr(tool_call, "function", None)
            name = _get_attr(fn, "name") or _get_attr(tool_call, "name") or ""
            arguments = _get_attr(fn, "arguments") or _get_attr(tool_call, "arguments")
            if isinstance(arguments, dict):
                arguments_value: str | dict[str, Any] | None = arguments
            else:
                arguments_value = str(arguments) if arguments is not None else None
            outputs.append(
                openai_models.FunctionCallOutput(
                    type="function_call",
                    id=getattr(tool_call, "id", f"call_{idx}"),
                    status="completed",
                    name=name,
                    call_id=getattr(tool_call, "id", None),
                    arguments=arguments_value,
                )
            )

    reasoning_summary = []
    for entry in reasoning_entries:
        summary_list = _get_attr(entry, "summary")
        if isinstance(summary_list, list):
            reasoning_summary.extend(summary_list)

    usage: openai_models.ResponseUsage | None = None
    if chat_response.usage:
        usage = convert__openai_completion_usage_to_openai_responses__usage(
            chat_response.usage
        )

    return openai_models.ResponseObject(
        id=chat_response.id or "resp-unknown",
        object="response",
        created_at=int(time.time()),
        model=chat_response.model or "",
        status="completed",
        output=outputs,
        parallel_tool_calls=False,
        usage=usage,
        reasoning=(
            openai_models.Reasoning(summary=reasoning_summary)
            if reasoning_summary
            else None
        ),
    )


def convert__openai_responses_to_openai_chat__response(
    response: openai_models.ResponseObject,
) -> openai_models.ChatCompletionResponse:
    """Convert an OpenAI ResponseObject to a ChatCompletionResponse."""
    text_segments: list[str] = []
    added_reasoning: set[tuple[str, str]] = set()
    tool_calls: list[openai_models.ToolCall] = []

    for item in response.output or []:
        logger.debug(
            "convert_responses_to_chat_response_item", item_type=_get_attr(item, "type")
        )
        item_type = _get_attr(item, "type")
        if item_type == "reasoning":
            for segment in _extract_reasoning_blocks(item):
                signature = segment.signature
                thinking_text = segment.thinking
                logger.debug(
                    "convert_responses_to_chat_reasoning_block",
                    signature=signature,
                    text_snippet=(thinking_text[:30] + "...")
                    if thinking_text and len(thinking_text) > 30
                    else thinking_text,
                )
                if thinking_text:
                    key = (signature or "", thinking_text)
                    if key not in added_reasoning:
                        text_segments.append(_wrap_thinking(signature, thinking_text))
                        added_reasoning.add(key)
        elif item_type == "message":
            parts: list[str] = []
            content_list = _get_attr(item, "content")
            if isinstance(content_list, list):
                for part in content_list:
                    part_type = _get_attr(part, "type")
                    if part_type == "output_text":
                        text_val = _get_attr(part, "text")
                        if isinstance(text_val, str):
                            parts.append(text_val)
                    elif isinstance(part, str):
                        parts.append(part)
            elif isinstance(content_list, str):
                parts.append(content_list)
            if parts:
                text_segments.append("".join(parts))
        elif item_type == "function_call":
            function_block = _get_attr(item, "function")
            name = _get_attr(function_block, "name") or _get_attr(item, "name")
            arguments_value: Any = _get_attr(item, "arguments")
            if arguments_value is None and isinstance(function_block, dict):
                arguments_value = function_block.get("arguments")

            if not isinstance(name, str) or not name:
                continue

            if isinstance(arguments_value, dict):
                arguments_str = json.dumps(arguments_value)
            elif isinstance(arguments_value, str):
                arguments_str = arguments_value
            else:
                arguments_str = json.dumps(arguments_value or {})

            tool_calls.append(
                openai_models.ToolCall(
                    id=_get_attr(item, "id")
                    or _get_attr(item, "call_id")
                    or f"call_{len(tool_calls)}",
                    type="function",
                    function=openai_models.FunctionCall(
                        name=name,
                        arguments=arguments_str,
                    ),
                )
            )

    text_content = "".join(text_segments)

    usage = None
    if response.usage:
        usage = convert__openai_responses_usage_to_openai_completion__usage(
            response.usage
        )

    finish_reason: Literal["stop", "length", "tool_calls", "content_filter"] = (
        "tool_calls" if tool_calls else "stop"
    )

    return openai_models.ChatCompletionResponse(
        id=response.id or "chatcmpl-resp",
        choices=[
            openai_models.Choice(
                index=0,
                message=openai_models.ResponseMessage(
                    role="assistant",
                    content=text_content,
                    tool_calls=tool_calls or None,
                ),
                finish_reason=finish_reason,
            )
        ],
        created=0,
        model=response.model or "",
        object="chat.completion",
        usage=usage
        or openai_models.CompletionUsage(
            prompt_tokens=0, completion_tokens=0, total_tokens=0
        ),
    )


def convert__openai_responses_to_openai_chat__stream(
    stream: AsyncIterator[openai_models.AnyStreamEvent],
) -> AsyncGenerator[openai_models.ChatCompletionChunk, None]:
    """Convert Response API stream events to ChatCompletionChunk events."""

    async def generator() -> AsyncGenerator[openai_models.ChatCompletionChunk, None]:
        model_id = ""
        role_sent = False

        # Track tool call state keyed by response item id
        tool_states: dict[str, dict[str, Any]] = {}
        tool_order: list[str] = []
        tool_delta_emitted = False
        saw_tool_event = False
        tool_candidates: list[tuple[str | None, set[str]]] = []
        reasoning_states: dict[str, dict[str, Any]] = {}

        def _ensure_reasoning_state(item_id: str) -> dict[str, Any]:
            state = reasoning_states.get(item_id)
            if state is None:
                state = {"parts": {}}
                reasoning_states[item_id] = state
            return state

        def _ensure_reasoning_part(item_id: str, summary_index: Any) -> dict[str, Any]:
            state = _ensure_reasoning_state(item_id)
            parts: dict[Any, dict[str, Any]] = state.setdefault("parts", {})
            part_state = parts.get(summary_index)
            if part_state is None:
                part_state = {"buffer": [], "signature": None}
                parts[summary_index] = part_state
            return part_state

        def _append_reasoning_text(
            item_id: str, summary_index: Any, text: str | None
        ) -> None:
            if not isinstance(text, str) or not text:
                return
            part_state = _ensure_reasoning_part(item_id, summary_index)
            part_state.setdefault("buffer", []).append(text)

        def _emit_reasoning_chunk(
            item_id: str, summary_index: Any, final_text: str | None = None
        ) -> list[str]:
            part_state = _ensure_reasoning_part(item_id, summary_index)
            buffer = part_state.setdefault("buffer", [])
            if isinstance(final_text, str) and final_text:
                text = final_text
            else:
                text = "".join(buffer)
            part_state["buffer"] = []
            if not text:
                return []
            signature = part_state.get("signature")
            part_state["open"] = False
            segment = ThinkingSegment(thinking=text, signature=signature)
            xml = segment.to_xml()
            closing = "</thinking>"
            if xml.endswith(closing):
                body = xml[: -len(closing)]
            else:
                body = xml
            return [body, closing]

        def _extract_tool_signature(tool_entry: Any) -> tuple[str | None, set[str]]:
            name: str | None = None
            param_keys: set[str] = set()

            if hasattr(tool_entry, "function"):
                fn = getattr(tool_entry, "function", None)
                if fn is not None:
                    name = getattr(fn, "name", None)
                    parameters = getattr(fn, "parameters", None)
                    if isinstance(parameters, dict):
                        props = parameters.get("properties")
                        if isinstance(props, dict):
                            param_keys = {str(key) for key in props}
            if name is None and isinstance(tool_entry, dict):
                fn_dict = tool_entry.get("function")
                if isinstance(fn_dict, dict):
                    name = fn_dict.get("name", name)
                    parameters = fn_dict.get("parameters")
                    if isinstance(parameters, dict):
                        props = parameters.get("properties")
                        if isinstance(props, dict):
                            param_keys = {str(key) for key in props}
                if name is None:
                    name = tool_entry.get("name")

            return name, param_keys

        def _guess_tool_name(arguments: str | None) -> str | None:
            if not arguments:
                return None
            try:
                parsed = json.loads(arguments)
            except Exception:
                return None
            if not isinstance(parsed, dict):
                return None
            keys = {str(k) for k in parsed}
            if not keys:
                return None

            candidates = [
                tool_name
                for tool_name, param_keys in tool_candidates
                if tool_name
                and ((param_keys and keys.issubset(param_keys)) or not param_keys)
            ]

            if len(candidates) == 1:
                return candidates[0]

            exact = [
                tool_name
                for tool_name, param_keys in tool_candidates
                if tool_name and param_keys == keys
            ]
            if len(exact) == 1:
                return exact[0]

            return None

        def _next_tool_index(item_id: str) -> int:
            if item_id not in tool_order:
                tool_order.append(item_id)
            return tool_order.index(item_id)

        def _ensure_tool_state(item_id: str) -> dict[str, Any]:
            state = tool_states.get(item_id)
            if state is None:
                index = _next_tool_index(item_id)
                state = {
                    "id": item_id,
                    "index": index,
                    "name": "",
                    "call_id": None,
                    "arguments": "",
                    "emitted": False,
                    "initial_emitted": False,
                    "name_emitted": False,
                    "arguments_emitted": False,
                    "completed": False,
                }
                tool_states[item_id] = state
            return state

        item_id = "msg_stream"
        output_index = 0
        content_index = 0
        sequence_counter = 0
        first_logged = False

        inline_reasoning_id = "__inline_reasoning__"
        inline_summary_index = "__inline__"

        async for event_wrapper in stream:
            evt = getattr(event_wrapper, "root", event_wrapper)
            if not hasattr(evt, "type"):
                continue

            logger.debug("stream_event", event_type=getattr(evt, "type", None))
            evt_type = getattr(evt, "type", "")

            if evt_type == "response.reasoning_summary_part.added":
                item_id = _get_attr(evt, "item_id")
                part = _get_attr(evt, "part")
                if isinstance(item_id, str) and item_id and part is not None:
                    summary_index = _get_attr(evt, "summary_index")
                    part_state = _ensure_reasoning_part(item_id, summary_index)
                    part_signature = _get_attr(part, "signature")
                    if isinstance(part_signature, str) and part_signature:
                        part_state["signature"] = part_signature
                    else:
                        part_type = _get_attr(part, "type")
                        part_text = _get_attr(part, "text")
                        if (
                            part_type == "signature"
                            and isinstance(part_text, str)
                            and part_text
                        ):
                            part_state["signature"] = part_text
                    part_state["buffer"] = []
                continue

            if evt_type in {
                "response.reasoning_summary_text.delta",
                "response.reasoning_text.delta",
            }:
                item_id = _get_attr(evt, "item_id")
                delta_text = _get_attr(evt, "delta")
                if isinstance(item_id, str):
                    summary_index = _get_attr(evt, "summary_index")
                    _append_reasoning_text(item_id, summary_index, delta_text)
                continue

            if evt_type in {
                "response.reasoning_summary_text.done",
                "response.reasoning_text.done",
            }:
                item_id = _get_attr(evt, "item_id")
                text_value = _get_attr(evt, "text")
                if isinstance(item_id, str):
                    summary_index = _get_attr(evt, "summary_index")
                    for chunk_text in _emit_reasoning_chunk(
                        item_id, summary_index, text_value
                    ):
                        sequence_counter += 1
                        yield openai_models.ChatCompletionChunk(
                            id="chatcmpl-stream",
                            created=0,
                            model=model_id,
                            choices=[
                                openai_models.StreamingChoice(
                                    index=0,
                                    delta=openai_models.DeltaMessage(
                                        role="assistant" if not role_sent else None,
                                        content=chunk_text,
                                    ),
                                    finish_reason=None,
                                )
                            ],
                        )
                        role_sent = True
                continue

            if evt_type == "response.created":
                response_obj = getattr(evt, "response", None)
                model_id = getattr(response_obj, "model", model_id) or model_id
                tools_metadata = getattr(response_obj, "tools", None)
                if not tools_metadata:
                    tools_metadata = get_last_request_tools() or []
                if tools_metadata:
                    tool_candidates = [
                        _extract_tool_signature(entry) for entry in tools_metadata
                    ]
                continue

            if evt_type == "response.output_text.delta":
                delta_text = getattr(evt, "delta", None) or ""
                if not delta_text:
                    continue

                remaining = delta_text

                # Directly create chunks and yield them instead of using a nested function
                # which has closure binding issues
                chunks_to_yield: list[openai_models.ChatCompletionChunk] = []

                def create_text_chunk(
                    current_model_id: str, text_segment: str, is_role_sent: bool
                ) -> tuple[openai_models.ChatCompletionChunk | None, bool]:
                    if not text_segment:
                        return None, is_role_sent
                    delta_msg = openai_models.DeltaMessage(
                        role="assistant" if not is_role_sent else None,
                        content=text_segment,
                    )
                    new_role_sent = True
                    chunk = openai_models.ChatCompletionChunk(
                        id="chatcmpl-stream",
                        created=0,
                        model=current_model_id,
                        choices=[
                            openai_models.StreamingChoice(
                                index=0,
                                delta=delta_msg,
                                finish_reason=None,
                            )
                        ],
                    )
                    return chunk, new_role_sent

                while remaining:
                    inline_part = _ensure_reasoning_part(
                        inline_reasoning_id, inline_summary_index
                    )
                    if inline_part.get("open"):
                        close_match = THINKING_CLOSE_PATTERN.search(remaining)
                        if close_match:
                            inside_text = remaining[: close_match.start()]
                            if inside_text:
                                _append_reasoning_text(
                                    inline_reasoning_id,
                                    inline_summary_index,
                                    inside_text,
                                )
                            for chunk_text in _emit_reasoning_chunk(
                                inline_reasoning_id, inline_summary_index
                            ):
                                chunk, role_sent = create_text_chunk(
                                    model_id, chunk_text, role_sent
                                )
                                if chunk:
                                    sequence_counter += 1
                                    chunks_to_yield.append(chunk)
                            inline_part["open"] = False
                            remaining = remaining[close_match.end() :]
                            continue
                        else:
                            _append_reasoning_text(
                                inline_reasoning_id,
                                inline_summary_index,
                                remaining,
                            )
                            remaining = ""
                            break

                    open_match = THINKING_OPEN_PATTERN.search(remaining)
                    if open_match:
                        prefix_text = remaining[: open_match.start()]
                        if prefix_text:
                            chunk, role_sent = create_text_chunk(
                                model_id, prefix_text, role_sent
                            )
                            if chunk:
                                sequence_counter += 1
                                chunks_to_yield.append(chunk)

                        signature = open_match.group(1) or None
                        inline_part = _ensure_reasoning_part(
                            inline_reasoning_id, inline_summary_index
                        )
                        if signature:
                            inline_part["signature"] = signature
                        remaining = remaining[open_match.end() :]

                        if inline_part.get("open"):
                            # Already inside a reasoning block; ignore duplicate tag
                            continue

                        inline_part["open"] = True
                        inline_part["buffer"] = []
                        continue

                    # No reasoning markers in the rest of the chunk
                    if inline_part.get("open"):
                        _append_reasoning_text(
                            inline_reasoning_id, inline_summary_index, remaining
                        )
                    else:
                        chunk, role_sent = create_text_chunk(
                            model_id, remaining, role_sent
                        )
                        if chunk:
                            sequence_counter += 1
                            chunks_to_yield.append(chunk)
                    remaining = ""

                for chunk in chunks_to_yield:
                    yield chunk
                continue

            if evt_type == "response.output_item.added":
                item = getattr(evt, "item", None)
                if not item:
                    continue

                item_type = getattr(item, "type", None)
                if item_type != "function_call":
                    continue

                saw_tool_event = True

                item_id_value = getattr(item, "id", None) or getattr(
                    item, "call_id", None
                )
                if not item_id_value:
                    item_id_value = f"call_{uuid.uuid4().hex}"
                item_id = item_id_value

                state = _ensure_tool_state(item_id)
                state["id"] = getattr(item, "id", state["id"]) or state["id"]
                state["call_id"] = getattr(item, "call_id", None) or state.get(
                    "call_id"
                )

                if not state.get("name") and state["index"] < len(tool_candidates):
                    candidate_name = tool_candidates[state["index"]][0]
                    if candidate_name:
                        state["name"] = candidate_name

                name = getattr(item, "name", None)
                if name:
                    state["name"] = name

                arguments = getattr(item, "arguments", None)
                if isinstance(arguments, str) and arguments:
                    state["arguments"] += arguments
                    if not state.get("name"):
                        guessed = _guess_tool_name(state["arguments"])
                        if guessed:
                            state["name"] = guessed

                # Emit initial tool call chunk to surface id/name information
                if not state.get("initial_emitted"):
                    tool_call = openai_models.ToolCall(
                        id=state["id"],
                        type="function",
                        function=openai_models.FunctionCall(
                            name=state.get("name") or "",
                            arguments=arguments or "",
                        ),
                    )
                    state["emitted"] = True
                    state["initial_emitted"] = True
                    if state.get("name"):
                        state["name_emitted"] = True
                    if arguments:
                        state["arguments_emitted"] = True

                    tool_delta_emitted = True

                    yield openai_models.ChatCompletionChunk(
                        id="chatcmpl-stream",
                        created=0,
                        model=model_id,
                        choices=[
                            openai_models.StreamingChoice(
                                index=0,
                                delta=openai_models.DeltaMessage(
                                    role="assistant" if not role_sent else None,
                                    tool_calls=[tool_call],
                                ),
                                finish_reason=None,
                            )
                        ],
                    )
                    role_sent = True
                continue

            if evt_type == "response.function_call_arguments.delta":
                saw_tool_event = True
                item_id_val = getattr(evt, "item_id", None)
                if not isinstance(item_id_val, str):
                    continue
                item_id = item_id_val
                delta_segment = getattr(evt, "delta", None)
                if not isinstance(delta_segment, str):
                    continue

                state = _ensure_tool_state(item_id)
                state["arguments"] += delta_segment
                if not state.get("name"):
                    guessed = _guess_tool_name(state["arguments"])
                    if guessed:
                        state["name"] = guessed

                if state.get("initial_emitted"):
                    tool_call = openai_models.ToolCall(
                        id=state["id"],
                        type="function",
                        function=openai_models.FunctionCall(
                            name=state.get("name") or "",
                            arguments=delta_segment,
                        ),
                    )

                    state["emitted"] = True
                    if delta_segment:
                        state["arguments_emitted"] = True

                    tool_delta_emitted = True

                    yield openai_models.ChatCompletionChunk(
                        id="chatcmpl-stream",
                        created=0,
                        model=model_id,
                        choices=[
                            openai_models.StreamingChoice(
                                index=0,
                                delta=openai_models.DeltaMessage(
                                    role="assistant" if not role_sent else None,
                                    tool_calls=[tool_call],
                                ),
                                finish_reason=None,
                            )
                        ],
                    )
                    role_sent = True
                continue

            if evt_type == "response.function_call_arguments.done":
                saw_tool_event = True
                item_id_val = getattr(evt, "item_id", None)
                if not isinstance(item_id_val, str):
                    continue
                item_id = item_id_val
                arguments = getattr(evt, "arguments", None)
                if not isinstance(arguments, str) or not arguments:
                    continue

                state = _ensure_tool_state(item_id)
                # Only emit a chunk if we never emitted arguments earlier
                if not state.get("arguments_emitted"):
                    state["arguments"] = arguments
                    if not state.get("name"):
                        guessed = _guess_tool_name(arguments)
                        if guessed:
                            state["name"] = guessed

                    tool_call = openai_models.ToolCall(
                        id=state["id"],
                        type="function",
                        function=openai_models.FunctionCall(
                            name=state.get("name") or "",
                            arguments=arguments,
                        ),
                    )

                    state["emitted"] = True
                    state["arguments_emitted"] = True

                    tool_delta_emitted = True

                    yield openai_models.ChatCompletionChunk(
                        id="chatcmpl-stream",
                        created=0,
                        model=model_id,
                        choices=[
                            openai_models.StreamingChoice(
                                index=0,
                                delta=openai_models.DeltaMessage(
                                    role="assistant" if not role_sent else None,
                                    tool_calls=[tool_call],
                                ),
                                finish_reason=None,
                            )
                        ],
                    )
                    role_sent = True
                continue

            if evt_type == "response.output_item.done":
                item = getattr(evt, "item", None)
                if not item:
                    continue

                item_type = getattr(item, "type", None)

                if item_type == "reasoning":
                    summary_list = getattr(item, "summary", None)
                    if isinstance(summary_list, list):
                        for entry in summary_list:
                            text = _get_attr(entry, "text")
                            signature = _get_attr(entry, "signature")
                            if isinstance(text, str) and text:
                                chunk_text = _wrap_thinking(signature, text)
                                sequence_counter += 1
                                yield openai_models.ChatCompletionChunk(
                                    id="chatcmpl-stream",
                                    created=0,
                                    model=model_id,
                                    choices=[
                                        openai_models.StreamingChoice(
                                            index=0,
                                            delta=openai_models.DeltaMessage(
                                                role="assistant"
                                                if not role_sent
                                                else None,
                                                content=chunk_text,
                                            ),
                                            finish_reason=None,
                                        )
                                    ],
                                )
                                role_sent = True
                    continue

                if item_type != "function_call":
                    continue

                saw_tool_event = True

                item_id_value = getattr(item, "id", None) or getattr(
                    item, "call_id", None
                )
                if not isinstance(item_id_value, str) or not item_id_value:
                    continue
                item_id = item_id_value

                state = _ensure_tool_state(item_id)
                name = getattr(item, "name", None)
                if name:
                    state["name"] = name
                arguments = getattr(item, "arguments", None)
                if isinstance(arguments, str) and arguments:
                    state["arguments"] = arguments
                    if not state.get("name"):
                        guessed = _guess_tool_name(arguments)
                        if guessed:
                            state["name"] = guessed
                    if not state.get("arguments_emitted"):
                        tool_call = openai_models.ToolCall(
                            id=state["id"],
                            type="function",
                            function=openai_models.FunctionCall(
                                name=state.get("name") or "",
                                arguments=arguments,
                            ),
                        )
                        state["emitted"] = True
                        state["arguments_emitted"] = True

                        yield openai_models.ChatCompletionChunk(
                            id="chatcmpl-stream",
                            created=0,
                            model=model_id,
                            choices=[
                                openai_models.StreamingChoice(
                                    index=0,
                                    delta=openai_models.DeltaMessage(
                                        role="assistant" if not role_sent else None,
                                        tool_calls=[tool_call],
                                    ),
                                    finish_reason=None,
                                )
                            ],
                        )
                        role_sent = True

                # Emit a patch chunk if the name was never surfaced earlier
                if state.get("name") and not state.get("name_emitted"):
                    tool_call = openai_models.ToolCall(
                        id=state["id"],
                        type="function",
                        function=openai_models.FunctionCall(
                            name=state.get("name") or "",
                            arguments="",
                        ),
                    )
                    state["name_emitted"] = True

                    tool_delta_emitted = True

                    yield openai_models.ChatCompletionChunk(
                        id="chatcmpl-stream",
                        created=0,
                        model=model_id,
                        choices=[
                            openai_models.StreamingChoice(
                                index=0,
                                delta=openai_models.DeltaMessage(
                                    role="assistant" if not role_sent else None,
                                    tool_calls=[tool_call],
                                ),
                                finish_reason=None,
                            )
                        ],
                    )
                    role_sent = True

                state["completed"] = True
                continue

            if evt_type in {
                "response.completed",
                "response.incomplete",
                "response.failed",
            }:
                usage = None
                response_obj = getattr(evt, "response", None)
                if response_obj and getattr(response_obj, "usage", None):
                    usage = convert__openai_responses_usage_to_openai_completion__usage(
                        response_obj.usage
                    )

                finish_reason: Literal["stop", "length", "tool_calls"] = "stop"
                if (
                    tool_delta_emitted
                    or saw_tool_event
                    or tool_states
                    or any(state.get("completed") for state in tool_states.values())
                ):
                    finish_reason = "tool_calls"

                yield openai_models.ChatCompletionChunk(
                    id="chatcmpl-stream",
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

                # Cleanup request tool cache context when stream completes
                register_request_tools(None)

    return generator()


def convert__openai_chat_to_openai_responses__stream(
    stream: AsyncIterator[openai_models.ChatCompletionChunk | dict[str, Any]],
) -> AsyncGenerator[
    openai_models.ResponseCreatedEvent
    | openai_models.ResponseInProgressEvent
    | openai_models.ResponseCompletedEvent
    | openai_models.ResponseOutputTextDeltaEvent,
    None,
]:
    """Convert OpenAI ChatCompletionChunk stream to Responses API events.

    Emits a minimal sequence: response.created (first chunk with model),
    response.output_text.delta for each delta content, optional
    response.in_progress with usage if present mid-stream, and a final
    response.completed when stream ends.
    """

    async def generator() -> AsyncGenerator[
        openai_models.ResponseCreatedEvent
        | openai_models.ResponseInProgressEvent
        | openai_models.ResponseCompletedEvent
        | openai_models.ResponseOutputTextDeltaEvent,
        None,
    ]:
        log = logger.bind(category="formatter", converter="chat_to_responses_stream")

        created_sent = False
        response_id = "chat-to-resp"
        item_id = "msg_stream"
        output_index = 0
        content_index = 0
        last_model = ""
        sequence_counter = 0
        first_logged = False

        async for chunk in stream:
            # Support both typed ChatCompletionChunk and dict-like payloads
            if isinstance(chunk, dict):
                model = chunk.get("model") or last_model
                choices = chunk.get("choices") or []
                usage_obj = chunk.get("usage")
                finish_reason = None
                if choices:
                    try:
                        finish_reason = choices[0].get("finish_reason")
                    except Exception:
                        finish_reason = None
                delta_text = None
                try:
                    delta = (choices[0] or {}).get("delta") if choices else None
                    delta_text = (delta or {}).get("content")
                except Exception:
                    delta_text = None
            else:
                model = getattr(chunk, "model", None) or last_model
                choices = getattr(chunk, "choices", [])
                usage_obj = getattr(chunk, "usage", None)
                finish_reason = None
                if choices:
                    first_choice = choices[0]
                    finish_reason = getattr(first_choice, "finish_reason", None)
                delta = None
                if choices:
                    first_choice = choices[0]
                    delta = getattr(first_choice, "delta", None)
                delta_text = getattr(delta, "content", None) if delta else None

            last_model = model

            if not first_logged:
                first_logged = True
                with contextlib.suppress(Exception):
                    log.debug(
                        "chat_stream_first_chunk",
                        typed=isinstance(chunk, dict) is False,
                        keys=(list(chunk.keys()) if isinstance(chunk, dict) else None),
                        has_delta=bool(delta_text),
                        model=model,
                    )

            # Emit created once we know model (or immediately on first chunk)
            if not created_sent:
                created_sent = True
                sequence_counter += 1
                yield openai_models.ResponseCreatedEvent(
                    type="response.created",
                    sequence_number=sequence_counter,
                    response=openai_models.ResponseObject(
                        id=response_id,
                        object="response",
                        created_at=0,
                        status="in_progress",
                        model=model or "",
                        output=[],
                        parallel_tool_calls=False,
                    ),
                )

            # Emit deltas for assistant content
            if isinstance(delta_text, str) and delta_text:
                sequence_counter += 1
                yield openai_models.ResponseOutputTextDeltaEvent(
                    type="response.output_text.delta",
                    sequence_number=sequence_counter,
                    item_id=item_id,
                    output_index=output_index,
                    content_index=content_index,
                    delta=delta_text,
                )
                content_index += 1

            # If usage arrives mid-stream and not finished, surface as in_progress
            if usage_obj and (finish_reason is None):
                try:
                    usage_model = (
                        convert__openai_completion_usage_to_openai_responses__usage(
                            usage_obj
                        )
                        if not isinstance(usage_obj, dict)
                        else convert__openai_completion_usage_to_openai_responses__usage(
                            openai_models.CompletionUsage.model_validate(usage_obj)
                        )
                    )
                    sequence_counter += 1
                    yield openai_models.ResponseInProgressEvent(
                        type="response.in_progress",
                        sequence_number=sequence_counter,
                        response=openai_models.ResponseObject(
                            id=response_id,
                            object="response",
                            created_at=0,
                            status="in_progress",
                            model=model or "",
                            output=[],
                            parallel_tool_calls=False,
                            usage=usage_model,
                        ),
                    )
                except Exception:
                    # best-effort; continue stream
                    pass

        # Final completion event
        sequence_counter += 1
        yield openai_models.ResponseCompletedEvent(
            type="response.completed",
            sequence_number=sequence_counter,
            response=openai_models.ResponseObject(
                id=response_id,
                object="response",
                created_at=0,
                status="completed",
                model=last_model,
                output=[],
                parallel_tool_calls=False,
            ),
        )

    return generator()


async def convert__openai_chat_to_openai_responses__request(
    request: openai_models.ChatCompletionRequest,
) -> openai_models.ResponseRequest:
    """Convert ChatCompletionRequest to ResponseRequest using typed models."""
    model = request.model
    max_out = request.max_completion_tokens
    if max_out is None:
        # Access via __dict__ to avoid triggering deprecated field warnings
        max_out = request.__dict__.get("max_tokens")

    # Find the last user message
    user_text: str | None = None
    for msg in reversed(request.messages or []):
        if msg.role == "user":
            content = msg.content
            if isinstance(content, list):
                texts = [
                    part.text
                    for part in content
                    if hasattr(part, "type")
                    and part.type == "text"
                    and hasattr(part, "text")
                ]
                user_text = " ".join([t for t in texts if t])
            else:
                user_text = content
            break

    input_data = []
    if user_text:
        input_msg = {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": user_text,
                }
            ],
        }
        input_data = [input_msg]

    payload_data: dict[str, Any] = {
        "model": model,
    }
    if max_out is not None:
        payload_data["max_output_tokens"] = int(max_out)
    if input_data:
        payload_data["input"] = input_data

    # Structured outputs: map Chat response_format to Responses text.format
    resp_fmt = request.response_format
    if resp_fmt is not None:
        if resp_fmt.type == "text":
            payload_data["text"] = {"format": {"type": "text"}}
        elif resp_fmt.type == "json_object":
            payload_data["text"] = {"format": {"type": "json_object"}}
        elif resp_fmt.type == "json_schema" and hasattr(resp_fmt, "json_schema"):
            js = resp_fmt.json_schema
            # Pass through name/schema/strict if provided
            fmt = {"type": "json_schema"}
            if js is not None:
                js_dict = js.model_dump() if hasattr(js, "model_dump") else js
                if js_dict is not None:
                    fmt.update(
                        {
                            k: v
                            for k, v in js_dict.items()
                            if k in {"name", "schema", "strict", "$defs", "description"}
                        }
                    )
            payload_data["text"] = {"format": fmt}

    tools = _convert_tools_chat_to_responses(request.tools)
    if tools:
        payload_data["tools"] = tools

    if request.tool_choice is not None:
        payload_data["tool_choice"] = _convert_tool_choice_chat_to_responses(
            request.tool_choice
        )

    reasoning_effort = None
    if isinstance(request.reasoning_effort, str) and request.reasoning_effort:
        reasoning_effort = request.reasoning_effort
    else:
        env_toggle = os.getenv("LLM__OPENAI_THINKING_XML")
        if env_toggle is None:
            env_toggle = os.getenv("OPENAI_STREAM_ENABLE_THINKING_SERIALIZATION")
        enable_thinking = True
        if env_toggle is not None:
            enable_thinking = env_toggle.strip().lower() not in {
                "0",
                "false",
                "no",
                "off",
            }
        if enable_thinking:
            reasoning_effort = "medium"

    if reasoning_effort:
        payload_data["reasoning"] = {"effort": reasoning_effort, "summary": "auto"}

    return openai_models.ResponseRequest.model_validate(payload_data)
