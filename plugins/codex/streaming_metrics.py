"""Codex-specific streaming metrics extraction utilities.

This module provides utilities for extracting token usage from
OpenAI/Codex streaming responses.
"""

import json
from typing import Any

import structlog

from plugins.common.streaming_base import BaseStreamingMetricsCollector


logger = structlog.get_logger(__name__)


def extract_usage_from_codex_chunk(chunk_data: Any) -> dict[str, Any] | None:
    """Extract usage information from OpenAI/Codex streaming response chunk.

    OpenAI/Codex sends usage information in the final streaming chunk where
    usage is not null. Earlier chunks have usage=null.

    Args:
        chunk_data: Streaming response chunk dictionary

    Returns:
        Dictionary with token counts or None if no usage found
    """
    if not isinstance(chunk_data, dict):
        return None

    # Extract model if present
    model = chunk_data.get("model")

    # Check for different Codex response formats
    # 1. Standard OpenAI format (chat.completion.chunk)
    object_type = chunk_data.get("object", "")
    usage = chunk_data.get("usage")

    if usage and object_type.startswith(("chat.completion", "codex.response")):
        # Extract basic tokens
        result = {
            "input_tokens": usage.get("prompt_tokens") or usage.get("input_tokens", 0),
            "output_tokens": usage.get("completion_tokens")
            or usage.get("output_tokens", 0),
            "total_tokens": usage.get("total_tokens"),
            "event_type": "openai_completion",
            "model": model,
        }

        # Extract detailed token information if available
        if "input_tokens_details" in usage:
            result["cache_read_tokens"] = usage["input_tokens_details"].get(
                "cached_tokens", 0
            )

        if "output_tokens_details" in usage:
            result["reasoning_tokens"] = usage["output_tokens_details"].get(
                "reasoning_tokens", 0
            )

        return result

    # 2. Codex CLI response format (response.completed event)
    event_type = chunk_data.get("type", "")
    if event_type == "response.completed" and "response" in chunk_data:
        response_data = chunk_data["response"]
        if isinstance(response_data, dict) and "usage" in response_data:
            usage = response_data["usage"]
            if usage:
                # Codex CLI uses various formats
                result = {
                    "input_tokens": usage.get("input_tokens")
                    or usage.get("prompt_tokens", 0),
                    "output_tokens": usage.get("output_tokens")
                    or usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens"),
                    "event_type": "codex_cli_response",
                    "model": response_data.get("model") or model,
                }

                # Check for detailed tokens
                if "input_tokens_details" in usage:
                    result["cache_read_tokens"] = usage["input_tokens_details"].get(
                        "cached_tokens", 0
                    )

                if "output_tokens_details" in usage:
                    result["reasoning_tokens"] = usage["output_tokens_details"].get(
                        "reasoning_tokens", 0
                    )

                return result

    return None


class CodexStreamingMetricsCollector(BaseStreamingMetricsCollector):
    """Collects and manages token metrics during Codex streaming responses.

    Implements IStreamingMetricsCollector interface for Codex/OpenAI.
    """

    def __init__(
        self,
        request_id: str | None = None,
        pricing_service: Any = None,
        model: str | None = None,
    ) -> None:
        """Initialize the metrics collector.

        Args:
            request_id: Optional request ID for logging context
            pricing_service: Optional pricing service for cost calculation
            model: Optional model name for cost calculation (can also be extracted from chunks)
        """
        super().__init__(request_id, pricing_service, model)
        self.reasoning_tokens: int | None = None  # Store reasoning tokens separately

    def process_raw_chunk(self, chunk_str: str) -> bool:
        """Process raw Codex format chunk before any conversion.

        This handles Codex's native response.completed event format.
        """
        return self.process_chunk(chunk_str)

    def process_converted_chunk(self, chunk_str: str) -> bool:
        """Process chunk after conversion to OpenAI format.

        When Codex responses are converted to OpenAI chat completion format,
        this method extracts metrics from the converted OpenAI format.
        """
        # After conversion, we'd see standard OpenAI format
        # For now, delegate to main process_chunk which handles both
        return self.process_chunk(chunk_str)

    def _extract_tokens_from_chunk(self, chunk_str: str) -> bool:
        """Codex-specific chunk parsing logic.

        Returns:
            True if this was the final chunk with complete metrics
        """
        # Check if this chunk contains usage information
        if "usage" not in chunk_str:
            return False

        logger.debug(
            "processing_chunk",
            chunk_preview=chunk_str[:300],
            request_id=self.request_id,
        )

        try:
            # Parse SSE data lines to find usage information
            # Codex sends complete JSON on a single line after "data: "
            for line in chunk_str.split("\n"):
                if line.startswith("data: "):
                    data_str = line[6:].strip()
                    if data_str and data_str != "[DONE]":
                        event_data = json.loads(data_str)

                        # Log event type for debugging
                        event_type = event_data.get("type", "")
                        if event_type == "response.completed":
                            logger.debug(
                                "completed_event_found",
                                has_response=("response" in event_data),
                                has_usage=("usage" in event_data.get("response", {}))
                                if "response" in event_data
                                else False,
                                request_id=self.request_id,
                            )

                        usage_data = extract_usage_from_codex_chunk(event_data)

                        if usage_data:
                            # Store token counts from the event
                            self.metrics["tokens_input"] = usage_data.get(
                                "input_tokens"
                            )
                            self.metrics["tokens_output"] = usage_data.get(
                                "output_tokens"
                            )
                            self.metrics["cache_read_tokens"] = usage_data.get(
                                "cache_read_tokens", 0
                            )
                            self.reasoning_tokens = usage_data.get(
                                "reasoning_tokens", 0
                            )

                            # Extract model from the chunk if we don't have it yet
                            if not self.model and usage_data.get("model"):
                                self.model = usage_data.get("model")
                                logger.debug(
                                    "model_extracted_from_stream",
                                    plugin="codex",
                                    model=self.model,
                                    request_id=self.request_id,
                                )

                            logger.debug(
                                "token_metrics_extracted",
                                plugin="codex",
                                tokens_input=self.metrics["tokens_input"],
                                tokens_output=self.metrics["tokens_output"],
                                cache_read_tokens=self.metrics["cache_read_tokens"],
                                reasoning_tokens=self.reasoning_tokens,
                                total_tokens=usage_data.get("total_tokens"),
                                event_type=usage_data.get("event_type"),
                                request_id=self.request_id,
                            )
                            return True  # This is the final event with complete metrics

                        break  # Only process first valid data line

        except (json.JSONDecodeError, KeyError) as e:
            logger.debug(
                "metrics_parse_failed",
                plugin="codex",
                error=str(e),
                request_id=self.request_id,
            )

        return False

    def get_reasoning_tokens(self) -> int | None:
        """Get reasoning tokens if available (for o1 models).

        Returns:
            Reasoning tokens count or None
        """
        return self.reasoning_tokens
