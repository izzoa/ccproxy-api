"""Built-in hook implementations for CCProxy.

This module contains standard hook implementations for common use cases:
- MetricsHook: Prometheus metrics collection
- LoggingHook: Structured logging
- AnalyticsHook: Analytics data collection
- AccessLoggingHook: Access logging (replaces AccessLogMiddleware)
- ContentLoggingHook: Content logging for hooks-based logging
- StreamingCaptureHook: Streaming response capture
- HTTPTracerHook: Core HTTP request/response tracing
"""

from .http_tracer import HTTPTracerHook


__all__: list[str] = ["HTTPTracerHook"]
