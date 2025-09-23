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
from typing import TYPE_CHECKING, Any

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
from ccproxy.config.codex import CodexSettings
from ccproxy.config.settings import get_settings
from ccproxy.services.model_info_service import get_model_info_service

if TYPE_CHECKING:  # pragma: no cover
    from ccproxy.services.model_info_service import ModelInfoService


logger = structlog.get_logger(__name__)


SUPPORTED_RESPONSE_MODELS: set[str] = {
    "gpt-5",
    "gpt-4o",
    "gpt-4o-mini",
    "o1",
    "o1-mini",
    "o1-preview",
    "o3-mini",
}

MODEL_ALIASES: dict[str, str] = {
    "chatgpt-4o-latest": "gpt-4o",
    "gpt-4o-latest": "gpt-4o",
    "gpt-4.1": "gpt-4o",
    "gpt-4.1-mini": "gpt-4o-mini",
    "gpt-4-turbo": "gpt-4o",
    "gpt-4": "gpt-4o",
    "gpt-3.5": "gpt-4o-mini",
    "gpt-3.5-turbo": "gpt-4o-mini",
    "gpt-3.5-turbo-0125": "gpt-4o-mini",
    "gpt-4o-realtime-preview": "gpt-4o",
    "o3": "o3-mini",
    "o3-mini-2024-05-20": "o3-mini",
}

DEFAULT_RESPONSE_MODEL = "gpt-4o"


class UnsupportedOpenAIParametersError(ValueError):
    """Raised when a Chat Completions request includes unsupported parameters."""

    def __init__(self, parameters: list[str]) -> None:
        self.parameters = parameters
        message = (
            "Unsupported OpenAI parameters for Codex: "
            + ", ".join(parameters)
        )
        super().__init__(message)


class UnsupportedCodexModelError(ValueError):
    """Raised when a requested Codex model is not supported."""

    def __init__(self, model: str, supported: list[str]) -> None:
        self.model = model
        self.supported = supported
        message = (
            f"Model '{model}' is not supported by Codex. Supported models: "
            + ", ".join(supported)
        )
        super().__init__(message)


class ResponseAdapter:
    """Adapter for OpenAI Response API format conversion."""

    def __init__(self, codex_settings: CodexSettings | None = None) -> None:
        self._codex_settings = codex_settings

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
        codex_settings = self._get_codex_settings()

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
        if not self._is_model_supported(response_model, set(SUPPORTED_RESPONSE_MODELS)):
            raise UnsupportedCodexModelError(response_model, sorted(SUPPORTED_RESPONSE_MODELS))

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

        # Apply parameter translation/validation (sync path falls back to configured default)
        self._apply_openai_parameters(
            chat_dict,
            request_dict,
            default_max_output_tokens=codex_settings.max_output_tokens_fallback,
        )

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
        codex_settings = self._get_codex_settings()

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
        default_dynamic_max: int | None = None
        model_info_service: "ModelInfoService" | None = None

        if codex_settings.enable_dynamic_model_info:
            try:
                model_info_service = get_model_info_service()
            except Exception as exc:
                logger.debug(
                    "codex_model_info_service_unavailable",
                    error=str(exc),
                )

        if max_tokens is None:
            if model_info_service is not None:
                try:
                    default_dynamic_max = await model_info_service.get_max_output_tokens(
                        response_model
                    )
                except Exception as exc:
                    logger.warning(
                        "failed_to_get_dynamic_max_tokens",
                        model=response_model,
                        error=str(exc),
                        fallback=codex_settings.max_output_tokens_fallback,
                    )
                    default_dynamic_max = codex_settings.max_output_tokens_fallback
            else:
                default_dynamic_max = codex_settings.max_output_tokens_fallback
        else:
            default_dynamic_max = max_tokens

        await self._ensure_model_supported(
            response_model,
            codex_settings,
            model_info_service=model_info_service,
        )

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

        # Apply parameter translation/validation using dynamic defaults
        self._apply_openai_parameters(
            chat_dict,
            request_dict,
            default_max_output_tokens=default_dynamic_max,
        )

        request = ResponseRequest(**request_dict)
        return request

    def _get_codex_settings(self) -> CodexSettings:
        if self._codex_settings is None:
            self._codex_settings = get_settings().codex
        return self._codex_settings

    def _map_to_response_api_model(self, model: str | None) -> str:
        """Map incoming OpenAI model identifier to Response API equivalent."""
        if not model:
            logger.debug("response_adapter_default_model", fallback=DEFAULT_RESPONSE_MODEL)
            return DEFAULT_RESPONSE_MODEL

        normalized = model.strip()
        alias_key = normalized.lower()
        mapped = MODEL_ALIASES.get(alias_key, normalized)

        # Allow suffix variations (e.g., gpt-4o-mini-2024-05-13)
        for candidate in SUPPORTED_RESPONSE_MODELS:
            if mapped == candidate or mapped.startswith(f"{candidate}-"):
                return candidate

        # If alias resolved to supported name, accept
        if mapped in SUPPORTED_RESPONSE_MODELS:
            return mapped

        logger.warning(
            "unsupported_codex_model_requested",
            requested=model,
            normalized=normalized,
            mapped=mapped,
        )
        raise UnsupportedCodexModelError(mapped, sorted(SUPPORTED_RESPONSE_MODELS))

    def _is_response_api_model_name(self, model_name: str | None) -> bool:
        if not model_name:
            return False
        model_lower = model_name.lower()
        return model_lower.startswith("gpt-") or model_lower.startswith("o1") or model_lower.startswith("o3")

    def _is_model_supported(self, model: str, supported: set[str]) -> bool:
        if model in supported:
            return True
        return any(model.startswith(candidate) for candidate in supported)

    async def _ensure_model_supported(
        self,
        model: str,
        settings: CodexSettings,
        model_info_service: "ModelInfoService" | None = None,
    ) -> None:
        """Ensure the mapped Response API model is supported."""

        supported_models = set(SUPPORTED_RESPONSE_MODELS)

        if settings.enable_dynamic_model_info:
            try:
                service = model_info_service or get_model_info_service()
                dynamic_models = await service.get_available_models()
                if dynamic_models:
                    supported_models.update(
                        {
                            m
                            for m in dynamic_models
                            if self._is_response_api_model_name(m)
                        }
                    )
            except Exception as exc:
                logger.debug(
                    "codex_dynamic_model_list_failed",
                    error=str(exc),
                )

        if not self._is_model_supported(model, supported_models):
            raise UnsupportedCodexModelError(model, sorted(supported_models))

    def _apply_openai_parameters(
        self,
        chat_dict: dict[str, Any],
        request_dict: dict[str, Any],
        default_max_output_tokens: int | None = None,
    ) -> None:
        """Translate OpenAI chat parameters into Response API fields.

        Args:
            chat_dict: Original OpenAI Chat Completion request data
            request_dict: Mutable Response API request dictionary
            default_max_output_tokens: Optional default when request omits max_tokens

        Raises:
            UnsupportedOpenAIParametersError: if unsupported parameters are present and
                propagation is disabled in Codex settings
        """

        settings = self._get_codex_settings()
        propagate = settings.propagate_unsupported_params

        ignored: list[str] = []
        blocked: list[str] = []

        def mark_unsupported(name: str, value: Any, reason: str) -> None:
            param_repr = f"{name}={value!r} ({reason})"
            if propagate:
                ignored.append(param_repr)
            else:
                blocked.append(param_repr)

        # max_tokens maps to Response API max_output_tokens; fall back to defaults
        if chat_dict.get("max_tokens") is not None:
            request_dict["max_output_tokens"] = chat_dict.get("max_tokens")
        else:
            fallback_value = default_max_output_tokens
            if fallback_value is None:
                fallback_value = settings.max_output_tokens_fallback
            if fallback_value is not None:
                request_dict.setdefault("max_output_tokens", fallback_value)

        # Standard sampling knobs
        if chat_dict.get("temperature") is not None:
            request_dict["temperature"] = chat_dict.get("temperature")
        if chat_dict.get("top_p") is not None:
            request_dict["top_p"] = chat_dict.get("top_p")

        # Penalties
        if chat_dict.get("frequency_penalty") is not None:
            request_dict["frequency_penalty"] = chat_dict.get("frequency_penalty")
        if chat_dict.get("presence_penalty") is not None:
            request_dict["presence_penalty"] = chat_dict.get("presence_penalty")

        # Seed is accepted for deterministic sampling
        if chat_dict.get("seed") is not None:
            request_dict["seed"] = chat_dict.get("seed")

        # Logit bias - ensure keys are strings for Response API compatibility
        if chat_dict.get("logit_bias"):
            bias = chat_dict.get("logit_bias", {})
            request_dict["logit_bias"] = {str(k): v for k, v in bias.items()}

        # Stop sequences map directly
        if chat_dict.get("stop") is not None:
            request_dict["stop"] = chat_dict.get("stop")

        # Store flag mirrors OpenAI behaviour when explicitly provided
        if chat_dict.get("store") is not None:
            request_dict["store"] = bool(chat_dict.get("store"))

        # Metadata + user identifier combine into metadata payload
        metadata = chat_dict.get("metadata") or {}
        user = chat_dict.get("user")
        if user:
            metadata = dict(metadata) if metadata else {}
            metadata.setdefault("user", user)
        if metadata:
            request_dict["metadata"] = metadata

        # Unsupported parameters -> warn or block
        if chat_dict.get("n") not in (None, 1):
            mark_unsupported("n", chat_dict.get("n"), "Response API only supports a single choice")

        if chat_dict.get("logprobs") is not None:
            mark_unsupported("logprobs", chat_dict.get("logprobs"), "log probabilities are unavailable in Codex Responses")

        if chat_dict.get("top_logprobs") is not None:
            mark_unsupported("top_logprobs", chat_dict.get("top_logprobs"), "Top log probabilities are unavailable in Codex Responses")

        if chat_dict.get("stream_options") is not None:
            mark_unsupported("stream_options", chat_dict.get("stream_options"), "stream options are not supported by Codex backend")

        if chat_dict.get("functions") is not None:
            mark_unsupported("functions", "deprecated", "Use the tools array instead of legacy functions")

        if chat_dict.get("function_call") is not None:
            mark_unsupported("function_call", chat_dict.get("function_call"), "Use tool_choice for function invocation control")

        # Raise if unsupported parameters present and cannot be ignored
        if blocked:
            raise UnsupportedOpenAIParametersError(blocked)

        if ignored:
            logger.warning(
                "response_adapter_ignored_unsupported_parameters",
                parameters=ignored,
                propagate="enabled",
            )

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
