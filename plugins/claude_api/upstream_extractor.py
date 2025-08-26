"""Upstream response extraction for Claude API responses."""

import json
from typing import Any


class ClaudeUpstreamExtractor:
    """Extracts usage and metadata from Anthropic API responses."""

    def extract_metadata(self, body: bytes, request_context: Any) -> None:
        try:
            response_data = json.loads(body)
            usage = response_data.get("usage", {})

            if not usage:
                return

            # Extract Anthropic-specific usage fields
            tokens_input = usage.get("input_tokens", 0)
            tokens_output = usage.get("output_tokens", 0)
            cache_read_tokens = usage.get("cache_read_input_tokens", 0)

            # Handle both old and new cache creation token formats
            cache_write_tokens = usage.get("cache_creation_input_tokens", 0)

            # New format has cache_creation as nested object
            if "cache_creation" in usage and isinstance(usage["cache_creation"], dict):
                cache_creation = usage["cache_creation"]
                cache_write_tokens = cache_creation.get(
                    "ephemeral_5m_input_tokens", 0
                ) + cache_creation.get("ephemeral_1h_input_tokens", 0)

            # Update request context with usage data
            if hasattr(request_context, "metadata"):
                request_context.metadata.update(
                    {
                        "tokens_input": tokens_input,
                        "tokens_output": tokens_output,
                        "tokens_total": tokens_input + tokens_output,
                        "cache_read_tokens": cache_read_tokens,
                        "cache_write_tokens": cache_write_tokens,
                    }
                )
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass  # Silent fail - usage extraction is non-critical
