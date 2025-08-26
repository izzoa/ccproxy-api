"""Upstream response extraction for Codex API responses."""

import json
from typing import Any


class CodexUpstreamExtractor:
    """Extracts usage and metadata from OpenAI/Codex API responses."""

    def extract_metadata(self, body: bytes, request_context: Any) -> None:
        try:
            response_data = json.loads(body)
            usage = response_data.get("usage", {})

            if not usage:
                return

            # Extract OpenAI-specific usage fields
            tokens_input = usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0)
            tokens_output = usage.get("completion_tokens", 0) or usage.get(
                "output_tokens", 0
            )

            # Check for cached tokens in input_tokens_details
            cache_read_tokens = 0
            if "input_tokens_details" in usage:
                cache_read_tokens = usage["input_tokens_details"].get(
                    "cached_tokens", 0
                )

            # Check for reasoning tokens in output_tokens_details
            reasoning_tokens = 0
            if "output_tokens_details" in usage:
                reasoning_tokens = usage["output_tokens_details"].get(
                    "reasoning_tokens", 0
                )

            # Update request context with usage data
            if hasattr(request_context, "metadata"):
                request_context.metadata.update(
                    {
                        "tokens_input": tokens_input,
                        "tokens_output": tokens_output,
                        "tokens_total": tokens_input + tokens_output,
                        "cache_read_tokens": cache_read_tokens,
                        "cache_write_tokens": 0,  # OpenAI doesn't have cache write
                        "reasoning_tokens": reasoning_tokens,
                    }
                )
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass  # Silent fail - usage extraction is non-critical
