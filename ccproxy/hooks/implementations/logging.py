"""Structured logging hook implementation."""

from typing import Any

import structlog

from ..base import HookContext
from ..events import HookEvent


class LoggingHook:
    """Structured logging for all events"""

    def __init__(self, logger: structlog.BoundLogger | None = None):
        """Initialize logging hook.

        Args:
            logger: Optional structlog logger instance. If None, creates a new one.
        """
        self.logger = logger or structlog.get_logger(__name__)
        self._name = "logging_hook"
        self._events = list(HookEvent)  # Log all events

    @property
    def name(self) -> str:
        """Hook name for debugging"""
        return self._name

    @property
    def events(self) -> list[HookEvent]:
        """Events this hook listens to"""
        return self._events

    async def __call__(self, context: HookContext) -> None:
        """Log event with structured context.

        Args:
            context: Hook context containing event data and metadata
        """
        # Build base log data
        log_data: dict[str, Any] = {
            "hook_event": context.event.value,
            "timestamp": context.timestamp.isoformat(),
        }

        # Add data and metadata
        if context.data:
            log_data["data"] = context.data
        if context.metadata:
            log_data["metadata"] = context.metadata

        # Add request context if available
        if context.request:
            log_data["request"] = {
                "method": context.request.method,
                "url": str(context.request.url),
                "headers": dict(context.request.headers),
            }
            # Add client IP if available
            if hasattr(context.request, "client") and context.request.client:
                log_data["request"]["client_ip"] = context.request.client.host

        # Add response context if available
        if context.response:
            log_data["response"] = {
                "status_code": context.response.status_code,
                "headers": dict(context.response.headers),
            }

        # Add provider/plugin information
        if context.provider:
            log_data["provider"] = context.provider
        if context.plugin:
            log_data["plugin"] = context.plugin

        # Add error information if available
        if context.error:
            log_data["error"] = {
                "type": type(context.error).__name__,
                "message": str(context.error),
            }
            # Add exception chain if available
            if hasattr(context.error, "__cause__") and context.error.__cause__:
                log_data["error"]["cause"] = {
                    "type": type(context.error.__cause__).__name__,
                    "message": str(context.error.__cause__),
                }

        # Determine log level based on event type
        log_level = self._get_log_level(context.event, context.error)

        # Log the event with structured data using proper message format
        if log_level == "error":
            self.logger.error("hook_event_error", **log_data)
        elif log_level == "warning":
            self.logger.warning("hook_event_warning", **log_data)
        elif log_level == "debug":
            self.logger.debug("hook_event_debug", **log_data)
        else:
            self.logger.info("hook_event", **log_data)

    def _get_log_level(self, event: HookEvent, error: Exception | None = None) -> str:
        """Determine appropriate log level for event.

        Args:
            event: The hook event
            error: Optional error context

        Returns:
            Log level string: 'error', 'warning', 'info', or 'debug'
        """
        # Error events always log as error
        if error or event in {
            HookEvent.REQUEST_FAILED,
            HookEvent.PROVIDER_ERROR,
            HookEvent.PLUGIN_ERROR,
        }:
            return "error"

        # Lifecycle events log as info
        if event in {
            HookEvent.APP_STARTUP,
            HookEvent.APP_SHUTDOWN,
            HookEvent.APP_READY,
            HookEvent.REQUEST_STARTED,
            HookEvent.REQUEST_COMPLETED,
            HookEvent.PLUGIN_LOADED,
            HookEvent.PLUGIN_UNLOADED,
        }:
            return "info"

        # Provider and streaming events log as debug (can be verbose)
        if event in {
            HookEvent.PROVIDER_REQUEST_SENT,
            HookEvent.PROVIDER_RESPONSE_RECEIVED,
            HookEvent.PROVIDER_STREAM_START,
            HookEvent.PROVIDER_STREAM_CHUNK,
            HookEvent.PROVIDER_STREAM_END,
        }:
            return "debug"

        # Custom events default to info
        return "info"
