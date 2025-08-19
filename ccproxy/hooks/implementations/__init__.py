"""Built-in hook implementations for CCProxy.

This module contains standard hook implementations for common use cases:
- MetricsHook: Prometheus metrics collection
- LoggingHook: Structured logging
- AnalyticsHook: Analytics data collection
"""

from .analytics import AnalyticsHook
from .logging import LoggingHook
from .metrics import MetricsHook


__all__ = ["AnalyticsHook", "LoggingHook", "MetricsHook"]
