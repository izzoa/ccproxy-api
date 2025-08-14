"""Plugin system for ccproxy."""

from ccproxy.plugins.protocol import ProviderPlugin
from ccproxy.plugins.registry import PluginRegistry


__all__ = ["ProviderPlugin", "PluginRegistry"]
