"""
Request context management with timing and correlation.

This module provides context managers and utilities for tracking request lifecycle,
timing measurements, and correlation across async operations. Uses structlog for
rich business event logging.

Key features:
- Accurate timing measurement using time.perf_counter()
- Request correlation with unique IDs
- Structured logging integration
- Async-safe context management with contextvars
- Exception handling and error tracking
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

from ccproxy.core.logging import TraceBoundLogger, get_logger


logger = get_logger(__name__)

# Context variable for async-safe request context propagation
request_context_var: ContextVar[RequestContext | None] = ContextVar(
    "request_context", default=None
)


@dataclass
class RequestContext:
    """
    Context object for tracking request state and metadata.

    Provides access to request ID, timing information, and structured logger
    with automatically injected context.
    """

    request_id: str
    start_time: float
    logger: structlog.stdlib.BoundLogger | TraceBoundLogger
    metadata: dict[str, Any] = field(default_factory=dict)
    storage: Any | None = None  # Optional DuckDB storage instance
    log_timestamp: datetime | None = None  # Datetime for consistent logging filenames
    metrics: dict[str, Any] = field(default_factory=dict)  # Request metrics storage
    format_chain: list[str] | None = None  # Format conversion chain
    model_metadata: Any | None = None  # Model metadata from registry (ModelCard)

    @property
    def duration_ms(self) -> float:
        """Get current duration in milliseconds."""
        return (time.perf_counter() - self.start_time) * 1000

    @property
    def duration_seconds(self) -> float:
        """Get current duration in seconds."""
        return time.perf_counter() - self.start_time

    def add_metadata(self, **kwargs: Any) -> None:
        """Add metadata to the request context."""
        self.metadata.update(kwargs)
        # Update logger context
        self.logger = self.logger.bind(**kwargs)

    def log_event(self, event: str, **kwargs: Any) -> None:
        """Log an event with current context and timing."""
        self.logger.info(
            event, request_id=self.request_id, duration_ms=self.duration_ms, **kwargs
        )

    def get_log_timestamp_prefix(self) -> str:
        """Get timestamp prefix for consistent log filenames.

        Returns:
            Timestamp string in YYYYMMDDhhmmss format (UTC)
        """
        if self.log_timestamp:
            return self.log_timestamp.strftime("%Y%m%d%H%M%S")
        else:
            # Fallback to current time if not set
            return datetime.now(UTC).strftime("%Y%m%d%H%M%S")

    def set_current(self) -> Token[RequestContext | None]:
        """Set this context as the current request context.

        Returns:
            Token that can be used to restore the previous context
        """
        return request_context_var.set(self)

    @staticmethod
    def get_current() -> RequestContext | None:
        """Get the current request context from async context.

        Returns:
            The current RequestContext or None if not set
        """
        return request_context_var.get()

    def clear_current(self, token: Token[RequestContext | None]) -> None:
        """Clear the current context and restore the previous one.

        Args:
            token: The token returned by set_current()
        """
        request_context_var.reset(token)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the context to a dictionary for JSON logging.

        Returns all context data including:
        - Request ID and timing information
        - All metadata (costs, tokens, model, etc.)
        - All metrics
        - Computed properties (duration_ms, duration_seconds)

        Excludes non-serializable fields like logger and storage.
        """
        # Start with basic fields
        data = {
            "request_id": self.request_id,
            "start_time": self.start_time,
        }

        # Add computed timing properties
        try:
            data["duration_ms"] = self.duration_ms
            data["duration_seconds"] = self.duration_seconds
        except Exception:
            pass

        # Add log timestamp if present
        if self.log_timestamp:
            try:
                data["log_timestamp"] = self.log_timestamp.isoformat()
            except Exception:
                data["log_timestamp"] = str(self.log_timestamp)

        # Add all metadata (includes costs, tokens, model info, etc.)
        if self.metadata:
            # Try to deep copy metadata to avoid reference issues
            try:
                # Ensure metadata is JSON serializable
                data["metadata"] = json.loads(json.dumps(self.metadata, default=str))
            except Exception:
                data["metadata"] = self.metadata

        # Add all metrics
        if self.metrics:
            try:
                # Ensure metrics is JSON serializable
                data["metrics"] = json.loads(json.dumps(self.metrics, default=str))
            except Exception:
                data["metrics"] = self.metrics

        return data


async def get_request_event_stream() -> AsyncGenerator[dict[str, Any], None]:
    """Async generator for request events used by analytics streaming.

    This is a lightweight stub for type-checking and optional runtime use.
    Integrations can replace or wrap this to provide actual event streams.
    """
    # Empty async generator
    for _ in ():
        yield {}


@asynccontextmanager
async def request_context(
    request_id: str | None = None,
    storage: Any | None = None,
    metrics: Any | None = None,
    log_timestamp: datetime | None = None,
    **initial_context: Any,
) -> AsyncGenerator[RequestContext, None]:
    """
    Context manager for tracking complete request lifecycle with timing.

    Automatically logs request start/success/error events with accurate timing.
    Provides structured logging with request correlation.

    Args:
        request_id: Unique request identifier (generated if not provided)
        storage: Optional storage backend for access logs
        metrics: Optional PrometheusMetrics instance for active request tracking
        **initial_context: Initial context to include in all log events

    Yields:
        RequestContext: Context object with timing and logging capabilities

    Example:
        async with request_context(method="POST", path="/v1/messages") as ctx:
            ctx.add_metadata(model="claude-3-5-sonnet")
            # Process request
            ctx.log_event("request_processed", tokens=150)
            # Context automatically logs success with timing
    """
    if request_id is None:
        request_id = str(uuid.uuid4())

    # Create logger with bound context
    request_logger = logger.bind(request_id=request_id, **initial_context)

    # Record start time
    start_time = time.perf_counter()

    # Log request start
    request_logger.debug(
        "request_start", request_id=request_id, timestamp=time.time(), **initial_context
    )

    # SSE events removed - functionality moved to plugins

    # Increment active requests if metrics provided
    if metrics:
        metrics.inc_active_requests()

    # Create context object
    ctx = RequestContext(
        request_id=request_id,
        start_time=start_time,
        logger=request_logger,
        metadata=dict(initial_context),
        storage=storage,
        log_timestamp=log_timestamp,
    )

    # Set as current context for async propagation
    token = ctx.set_current()

    try:
        yield ctx

        # Log successful completion with comprehensive access log
        duration_ms = ctx.duration_ms

        # Also keep the original request_success event for debugging
        # Merge metadata, avoiding duplicates
        success_log_data = {
            "request_id": request_id,
            "duration_ms": duration_ms,
            "duration_seconds": ctx.duration_seconds,
        }

        # Add metadata, avoiding duplicates
        for key, value in ctx.metadata.items():
            if key not in ("duration_ms", "duration_seconds", "request_id"):
                success_log_data[key] = value

        request_logger.debug(
            "request_success",
            **success_log_data,
        )

    except Exception as e:
        # Log error with timing
        duration_ms = ctx.duration_ms
        error_type = type(e).__name__

        # Merge metadata but ensure no duplicate duration fields
        log_data = {
            "request_id": request_id,
            "duration_ms": duration_ms,
            "duration_seconds": ctx.duration_seconds,
            "error_type": error_type,
            "error_message": str(e),
        }

        # Add metadata, avoiding duplicates
        for key, value in ctx.metadata.items():
            if key not in ("duration_ms", "duration_seconds"):
                log_data[key] = value

        request_logger.error(
            "request_error",
            exc_info=e,
            **log_data,
        )

        # SSE events removed - functionality moved to plugins

        # Re-raise the exception
        raise
    finally:
        # Clear the current context
        ctx.clear_current(token)

        # Decrement active requests if metrics provided
        if metrics:
            metrics.dec_active_requests()


@asynccontextmanager
async def timed_operation(
    operation_name: str, request_id: str | None = None, **context: Any
) -> AsyncGenerator[dict[str, Any], None]:
    """
    Context manager for timing individual operations within a request.

    Useful for measuring specific parts of request processing like
    API calls, database queries, or data processing steps.

    Args:
        operation_name: Name of the operation being timed
        request_id: Associated request ID for correlation
        **context: Additional context for logging

    Yields:
        Dict with timing information and logger

    Example:
        async with timed_operation("claude_api_call", request_id=ctx.request_id) as op:
            response = await api_client.call()
            op["response_size"] = len(response)
            # Automatically logs operation timing
    """
    start_time = time.perf_counter()
    operation_id = str(uuid.uuid4())

    # Create operation logger
    op_logger = logger.bind(
        operation_name=operation_name,
        operation_id=operation_id,
        request_id=request_id,
        **context,
    )

    # Log operation start (only for important operations)
    if operation_name in ("claude_api_call", "request_processing", "auth_check"):
        op_logger.debug(
            "operation_start",
            operation_name=operation_name,
            **context,
        )

    # Operation context
    op_context = {
        "operation_id": operation_id,
        "logger": op_logger,
        "start_time": start_time,
    }

    try:
        yield op_context

        # Log successful completion (only for important operations)
        duration_ms = (time.perf_counter() - start_time) * 1000
        if operation_name in ("claude_api_call", "request_processing", "auth_check"):
            op_logger.info(
                "operation_success",
                operation_name=operation_name,
                duration_ms=duration_ms,
                **{
                    k: v
                    for k, v in op_context.items()
                    if k not in ("logger", "start_time")
                },
            )

    except Exception as e:
        # Log operation error
        duration_ms = (time.perf_counter() - start_time) * 1000
        error_type = type(e).__name__

        op_logger.error(
            "operation_error",
            operation_name=operation_name,
            duration_ms=duration_ms,
            error_type=error_type,
            error_message=str(e),
            exc_info=e,
            **{
                k: v for k, v in op_context.items() if k not in ("logger", "start_time")
            },
        )

        # Re-raise the exception
        raise


class ContextTracker:
    """
    Thread-safe tracker for managing active request contexts.

    Useful for tracking concurrent requests and their states,
    especially for metrics like active request counts.
    """

    def __init__(self) -> None:
        self._active_contexts: dict[str, RequestContext] = {}
        self._lock = asyncio.Lock()

    async def add_context(self, context: RequestContext) -> None:
        """Add an active request context."""
        async with self._lock:
            self._active_contexts[context.request_id] = context

    async def remove_context(self, request_id: str) -> RequestContext | None:
        """Remove and return a request context."""
        async with self._lock:
            return self._active_contexts.pop(request_id, None)

    async def get_context(self, request_id: str) -> RequestContext | None:
        """Get a request context by ID."""
        async with self._lock:
            return self._active_contexts.get(request_id)

    async def get_active_count(self) -> int:
        """Get the number of active requests."""
        async with self._lock:
            return len(self._active_contexts)

    async def get_all_contexts(self) -> dict[str, RequestContext]:
        """Get a copy of all active contexts."""
        async with self._lock:
            return self._active_contexts.copy()

    async def cleanup_stale_contexts(self, max_age_seconds: float = 300) -> int:
        """
        Remove contexts older than max_age_seconds.

        Args:
            max_age_seconds: Maximum age in seconds before considering stale

        Returns:
            Number of contexts removed
        """
        current_time = time.perf_counter()
        removed_count = 0

        async with self._lock:
            stale_ids = [
                request_id
                for request_id, ctx in self._active_contexts.items()
                if (current_time - ctx.start_time) > max_age_seconds
            ]

            for request_id in stale_ids:
                del self._active_contexts[request_id]
                removed_count += 1

        if removed_count > 0:
            logger.warning(
                "cleanup_stale_contexts",
                removed_count=removed_count,
                max_age_seconds=max_age_seconds,
            )

        return removed_count


# Global context tracker instance
_global_tracker: ContextTracker | None = None


def get_context_tracker() -> ContextTracker:
    """Get or create global context tracker."""
    global _global_tracker

    if _global_tracker is None:
        _global_tracker = ContextTracker()

    return _global_tracker


@asynccontextmanager
async def tracked_request_context(
    request_id: str | None = None, storage: Any | None = None, **initial_context: Any
) -> AsyncGenerator[RequestContext, None]:
    """
    Request context manager that also tracks active requests globally.

    Combines request_context() with automatic tracking in the global
    context tracker for monitoring active request counts.

    Args:
        request_id: Unique request identifier
        **initial_context: Initial context to include in log events

    Yields:
        RequestContext: Context object with timing and logging
    """
    tracker = get_context_tracker()

    async with request_context(request_id, storage=storage, **initial_context) as ctx:
        # Add to tracker
        await tracker.add_context(ctx)

        try:
            yield ctx
        finally:
            # Remove from tracker
            await tracker.remove_context(ctx.request_id)
