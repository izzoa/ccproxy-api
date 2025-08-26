"""Base class for streaming metrics collection with shared cost calculation."""

from abc import ABC, abstractmethod
from typing import Any

import structlog

from ccproxy.streaming.interfaces import StreamingMetrics
from plugins.pricing.helper import safe_calculate_cost


class BaseStreamingMetricsCollector(ABC):
    """Base class for streaming metrics collection with shared cost calculation."""

    def __init__(
        self,
        request_id: str | None = None,
        pricing_service: Any = None,
        model: str | None = None,
    ):
        self.request_id = request_id
        self.pricing_service = pricing_service
        self.model = model
        self.logger = structlog.get_logger(self.__class__.__name__)
        self.metrics: StreamingMetrics = {
            "tokens_input": None,
            "tokens_output": None,
            "cache_read_tokens": None,
            "cache_write_tokens": None,
            "cost_usd": None,
        }

    @abstractmethod
    def _extract_tokens_from_chunk(self, chunk_str: str) -> bool:
        """Plugin-specific chunk parsing logic.

        Returns:
            True if this was the final chunk with complete metrics
        """
        raise NotImplementedError

    def process_chunk(self, chunk_str: str) -> bool:
        """Process a streaming chunk to extract token metrics."""
        is_final = self._extract_tokens_from_chunk(chunk_str)

        if is_final and self.pricing_service and self.model:
            self.metrics["cost_usd"] = safe_calculate_cost(
                pricing_service=self.pricing_service,
                model=self.model,
                tokens_input=self.metrics.get("tokens_input") or 0,
                tokens_output=self.metrics.get("tokens_output") or 0,
                cache_read_tokens=self.metrics.get("cache_read_tokens") or 0,
                cache_write_tokens=self.metrics.get("cache_write_tokens") or 0,
                logger=self.logger,
                log_ctx={
                    "plugin": self.__class__.__module__.split(".")[1],
                    "request_id": self.request_id,
                },
            )

        return is_final

    def get_metrics(self) -> StreamingMetrics:
        """Get the current collected metrics."""
        return self.metrics.copy()
