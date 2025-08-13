"""Plugin system for ccproxy."""

from .loader import PluginLoader
from .protocol import HealthCheckResult, ProviderPlugin
from .registry import PluginRegistry


__all__ = [
    "PluginLoader",
    "PluginRegistry",
    "ProviderPlugin",
    "HealthCheckResult",
]
