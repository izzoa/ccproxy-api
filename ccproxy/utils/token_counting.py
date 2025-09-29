"""Token counting utilities for various LLM providers."""

import json
import threading
from typing import Any

from ccproxy.core.logging import get_logger


logger = get_logger(__name__)

# Token counting constants
CHARS_PER_TOKEN = 4  # Rough approximation; prefer tiktoken if available
IMAGE_TOKEN_OVERHEAD = 85  # Heuristic for vision content
MESSAGE_FORMATTING_OVERHEAD = 4  # Tokens per message for formatting
ANTHROPIC_MESSAGE_OVERHEAD = 3  # Anthropic-specific message overhead
COMPLETION_TOKENS_OVERHEAD = 2  # Overhead for completion priming


class TokenCounter:
    """Token counting for different model providers."""

    def __init__(self) -> None:
        """Initialize token counter with optional tiktoken support."""
        self._tiktoken_available = False
        try:
            import tiktoken

            self._tiktoken = tiktoken
            self._tiktoken_available = True
        except ImportError:
            logger.warning(
                "tiktoken_not_available",
                message="Token counting will use approximations. Install tiktoken for accurate counts.",
            )

    def count_tokens(self, text: str, model: str = "gpt-4") -> int:
        """Count tokens in text for a specific model.

        Args:
            text: Text to count tokens for
            model: Model name (for provider-specific encoding)

        Returns:
            Approximate token count
        """
        if self._tiktoken_available:
            return self._count_with_tiktoken(text, model)
        return self._count_approximate(text)

    def _count_with_tiktoken(self, text: str, model: str) -> int:
        """Count tokens using tiktoken (accurate for OpenAI models)."""
        try:
            try:
                encoding = self._tiktoken.encoding_for_model(model)
            except KeyError:
                encoding = self._tiktoken.get_encoding("cl100k_base")

            return len(encoding.encode(text))
        except Exception as e:
            logger.warning("tiktoken_encoding_failed", error=str(e), model=model)
            return self._count_approximate(text)

    def _count_approximate(self, text: str) -> int:
        """Approximate token count using character-based heuristic."""
        return len(text) // CHARS_PER_TOKEN

    def count_messages_tokens(
        self, messages: list[dict[str, Any]], model: str = "gpt-4"
    ) -> int:
        """Count tokens in a list of messages.

        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Model name

        Returns:
            Total token count including message formatting overhead
        """
        total_tokens = 0

        for message in messages:
            total_tokens += MESSAGE_FORMATTING_OVERHEAD

            role = message.get("role", "")
            total_tokens += self.count_tokens(role, model)

            content = message.get("content", "")
            if isinstance(content, str):
                total_tokens += self.count_tokens(content, model)
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "text":
                            text = item.get("text", "")
                            total_tokens += self.count_tokens(text, model)
                        elif (
                            item.get("type") == "image_url"
                            or item.get("type") == "image"
                        ):
                            total_tokens += IMAGE_TOKEN_OVERHEAD

            name = message.get("name")
            if name:
                total_tokens += self.count_tokens(name, model)
                total_tokens -= 1

            function_call = message.get("function_call")
            if function_call:
                if isinstance(function_call, dict):
                    func_name = function_call.get("name", "")
                    func_args = function_call.get("arguments", "")
                    total_tokens += self.count_tokens(func_name, model)
                    total_tokens += self.count_tokens(func_args, model)

            tool_calls = message.get("tool_calls")
            if tool_calls and isinstance(tool_calls, list):
                for tool_call in tool_calls:
                    if isinstance(tool_call, dict):
                        func = tool_call.get("function", {})
                        if isinstance(func, dict):
                            func_name = func.get("name", "")
                            func_args = func.get("arguments", "")
                            total_tokens += self.count_tokens(func_name, model)
                            total_tokens += self.count_tokens(func_args, model)

        total_tokens += COMPLETION_TOKENS_OVERHEAD

        return total_tokens

    def count_anthropic_messages_tokens(
        self, messages: list[dict[str, Any]], system: str | None = None
    ) -> int:
        """Count tokens for Anthropic/Claude messages format.

        Args:
            messages: List of message dicts
            system: System prompt (if any)

        Returns:
            Approximate token count
        """
        total_tokens = 0

        if system:
            total_tokens += self.count_tokens(system, "claude-3")

        for message in messages:
            total_tokens += ANTHROPIC_MESSAGE_OVERHEAD

            role = message.get("role", "")
            total_tokens += self.count_tokens(role, "claude-3")

            content = message.get("content", "")
            if isinstance(content, str):
                total_tokens += self.count_tokens(content, "claude-3")
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "text":
                            text = item.get("text", "")
                            total_tokens += self.count_tokens(text, "claude-3")
                        elif item.get("type") == "image":
                            total_tokens += IMAGE_TOKEN_OVERHEAD
                        elif item.get("type") == "tool_use":
                            tool_input = item.get("input", {})
                            if isinstance(tool_input, dict):
                                total_tokens += self.count_tokens(
                                    json.dumps(tool_input), "claude-3"
                                )
                        elif item.get("type") == "tool_result":
                            tool_result = item.get("content", "")
                            if isinstance(tool_result, str):
                                total_tokens += self.count_tokens(
                                    tool_result, "claude-3"
                                )

        return total_tokens


_global_counter: TokenCounter | None = None
_counter_lock = threading.Lock()


def get_token_counter() -> TokenCounter:
    """Get global token counter singleton.

    Returns:
        Global TokenCounter instance
    """
    global _global_counter
    if _global_counter is None:
        with _counter_lock:
            if _global_counter is None:
                _global_counter = TokenCounter()
    return _global_counter


def count_tokens(text: str, model: str = "gpt-4") -> int:
    """Convenience function to count tokens in text.

    Args:
        text: Text to count
        model: Model name

    Returns:
        Token count
    """
    counter = get_token_counter()
    return counter.count_tokens(text, model)


def count_messages_tokens(
    messages: list[dict[str, Any]], model: str = "gpt-4", system: str | None = None
) -> int:
    """Convenience function to count tokens in messages.

    Args:
        messages: List of messages
        model: Model name
        system: System prompt (for Anthropic models)

    Returns:
        Total token count
    """
    counter = get_token_counter()

    if model.startswith("claude-"):
        return counter.count_anthropic_messages_tokens(messages, system)

    return counter.count_messages_tokens(messages, model)
