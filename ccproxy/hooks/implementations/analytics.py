"""Analytics hook implementation for collecting usage data."""

import asyncio
from typing import Any

import structlog

from ..base import HookContext
from ..events import HookEvent


class AnalyticsHook:
    """Collects analytics data with batched transmission.

    Listens to REQUEST_COMPLETED and PROVIDER_STREAM_END events to collect:
    - Request patterns and frequencies
    - Usage statistics by provider
    - Token consumption metrics
    - Model usage statistics

    Data is buffered and transmitted in batches for efficiency.
    """

    def __init__(self, batch_size: int = 100) -> None:
        """Initialize analytics hook with configurable batch size.

        Args:
            batch_size: Number of events to buffer before triggering transmission
        """
        self._name = "analytics_hook"
        self._events = [
            HookEvent.REQUEST_COMPLETED,
            HookEvent.PROVIDER_STREAM_END,
        ]
        self._buffer: list[dict[str, Any]] = []
        self._batch_size = batch_size
        self._logger = structlog.get_logger(__name__)
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        """Hook name for debugging."""
        return self._name

    @property
    def events(self) -> list[HookEvent]:
        """Events this hook listens to."""
        return self._events

    async def __call__(self, context: HookContext) -> None:
        """Collect analytics data from hook context.

        Args:
            context: Hook context containing event data
        """
        try:
            analytics_data = await self._extract_analytics_data(context)
            if analytics_data:
                await self._add_to_buffer(analytics_data)
        except Exception as e:
            self._logger.error(
                "Failed to collect analytics data", event=context.event, error=str(e)
            )

    async def _extract_analytics_data(
        self, context: HookContext
    ) -> dict[str, Any] | None:
        """Extract relevant analytics data from hook context.

        Args:
            context: Hook context containing event and request data

        Returns:
            Dictionary of analytics data or None if no relevant data
        """
        base_data = {
            "event": context.event,
            "timestamp": context.timestamp.isoformat(),
            "provider": context.provider,
            "plugin": context.plugin,
        }

        if context.event == HookEvent.REQUEST_COMPLETED:
            return await self._extract_request_analytics(context, base_data)
        elif context.event == HookEvent.PROVIDER_STREAM_END:
            return await self._extract_stream_analytics(context, base_data)

        return None

    async def _extract_request_analytics(
        self, context: HookContext, base_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Extract analytics data from completed request.

        Args:
            context: Hook context for REQUEST_COMPLETED event
            base_data: Base analytics data common to all events

        Returns:
            Analytics data for request completion
        """
        analytics_data = base_data.copy()

        # Request pattern data
        if context.request:
            analytics_data.update(
                {
                    "method": context.request.method,
                    "path": str(context.request.url.path),
                    "user_agent": context.request.headers.get("user-agent"),
                    "content_length": context.request.headers.get("content-length"),
                }
            )

        # Response data
        if context.response:
            analytics_data.update(
                {
                    "status_code": context.response.status_code,
                    "response_size": len(getattr(context.response, "body", b"")),
                }
            )

        # Performance metrics from context data
        if context.data:
            analytics_data.update(
                {
                    "duration_ms": context.data.get("duration"),
                    "status": context.data.get("status"),
                }
            )

        # Token consumption (if available in context data)
        if context.data and "tokens" in context.data:
            analytics_data["tokens"] = context.data["tokens"]

        # Model information (if available)
        if context.data and "model" in context.data:
            analytics_data["model"] = context.data["model"]

        return analytics_data

    async def _extract_stream_analytics(
        self, context: HookContext, base_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Extract analytics data from streaming response end.

        Args:
            context: Hook context for PROVIDER_STREAM_END event
            base_data: Base analytics data common to all events

        Returns:
            Analytics data for stream completion
        """
        analytics_data = base_data.copy()

        # Stream-specific data
        if context.data:
            analytics_data.update(
                {
                    "stream_duration_ms": context.data.get("stream_duration"),
                    "chunks_sent": context.data.get("chunks_sent"),
                    "total_tokens": context.data.get("total_tokens"),
                    "completion_tokens": context.data.get("completion_tokens"),
                    "prompt_tokens": context.data.get("prompt_tokens"),
                    "model": context.data.get("model"),
                }
            )

        return analytics_data

    async def _add_to_buffer(self, analytics_data: dict[str, Any]) -> None:
        """Add analytics data to buffer and trigger batch processing if needed.

        Args:
            analytics_data: Analytics data to buffer
        """
        async with self._lock:
            self._buffer.append(analytics_data)

            if len(self._buffer) >= self._batch_size:
                # Create a copy of the buffer for processing
                batch_data = self._buffer.copy()
                self._buffer.clear()

                # Process batch asynchronously without blocking
                asyncio.create_task(self._process_batch(batch_data))

    async def _process_batch(self, batch_data: list[dict[str, Any]]) -> None:
        """Process a batch of analytics data.

        Args:
            batch_data: List of analytics data entries to process
        """
        try:
            self._logger.info("Processing analytics batch", batch_size=len(batch_data))

            # Aggregate batch statistics
            batch_stats = await self._aggregate_batch_stats(batch_data)

            # Transmit batch data (stub implementation)
            await self._transmit_batch(batch_data, batch_stats)

        except Exception as e:
            self._logger.error(
                "Failed to process analytics batch",
                batch_size=len(batch_data),
                error=str(e),
            )

    async def _aggregate_batch_stats(
        self, batch_data: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Aggregate statistics for a batch of analytics data.

        Args:
            batch_data: List of analytics data entries

        Returns:
            Aggregated statistics for the batch
        """
        stats: dict[str, Any] = {
            "total_events": len(batch_data),
            "event_types": {},
            "providers": {},
            "models": {},
            "status_codes": {},
            "total_tokens": 0,
        }

        for entry in batch_data:
            # Count event types
            event_type = entry.get("event", "unknown")
            event_types = stats["event_types"]
            event_types[event_type] = event_types.get(event_type, 0) + 1

            # Count providers
            provider = entry.get("provider", "unknown")
            providers = stats["providers"]
            providers[provider] = providers.get(provider, 0) + 1

            # Count models
            model = entry.get("model")
            if model:
                models = stats["models"]
                models[model] = models.get(model, 0) + 1

            # Count status codes
            status_code = entry.get("status_code")
            if status_code:
                status_codes = stats["status_codes"]
                status_codes[str(status_code)] = (
                    status_codes.get(str(status_code), 0) + 1
                )

            # Sum token usage
            if "tokens" in entry:
                tokens = entry["tokens"]
                if isinstance(tokens, dict):
                    stats["total_tokens"] += tokens.get("total", 0)
                elif isinstance(tokens, int | float):
                    stats["total_tokens"] += tokens

            if "total_tokens" in entry:
                stats["total_tokens"] += entry.get("total_tokens", 0)

        return stats

    async def _transmit_batch(
        self, batch_data: list[dict[str, Any]], batch_stats: dict[str, Any]
    ) -> None:
        """Transmit analytics batch to external system.

        This is a stub implementation that logs the batch data.
        In a real implementation, this would send data to an analytics service,
        database, or message queue.

        Args:
            batch_data: List of analytics data entries
            batch_stats: Aggregated statistics for the batch
        """
        self._logger.info(
            "Transmitting analytics batch",
            batch_stats=batch_stats,
            sample_entries=batch_data[:3],  # Log first 3 entries as sample
        )

        # TODO: Implement actual transmission logic:
        # - Send to analytics service (e.g., Google Analytics, Mixpanel)
        # - Store in database for later analysis
        # - Send to message queue for async processing
        # - Export to data warehouse

        # For now, we just log that transmission would occur
        await asyncio.sleep(0.01)  # Simulate async transmission delay

    async def flush_buffer(self) -> None:
        """Flush any remaining data in the buffer.

        This method should be called during application shutdown
        to ensure no analytics data is lost.
        """
        async with self._lock:
            if self._buffer:
                batch_data = self._buffer.copy()
                self._buffer.clear()

                self._logger.info(
                    "Flushing remaining analytics buffer",
                    remaining_entries=len(batch_data),
                )

                await self._process_batch(batch_data)

    def get_buffer_size(self) -> int:
        """Get current buffer size for monitoring.

        Returns:
            Number of entries currently in the buffer
        """
        return len(self._buffer)

    def get_batch_size(self) -> int:
        """Get configured batch size.

        Returns:
            Configured batch size threshold
        """
        return self._batch_size
