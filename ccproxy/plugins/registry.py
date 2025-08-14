"""Plugin registry for managing provider plugins."""

import importlib.util
from pathlib import Path

import structlog

from ccproxy.plugins.protocol import ProviderPlugin
from ccproxy.services.adapters.base import BaseAdapter


logger = structlog.get_logger(__name__)


class PluginRegistry:
    """Registry for provider plugins."""

    def __init__(self) -> None:
        """Initialize plugin registry."""
        self._plugins: dict[str, ProviderPlugin] = {}
        self._adapters: dict[str, BaseAdapter] = {}

    async def discover(self, plugin_dir: Path) -> None:
        """Discover plugins in directory.

        Args:
            plugin_dir: Directory to search for plugins
        """
        if not plugin_dir.exists():
            logger.debug(f"Plugin directory does not exist: {plugin_dir}")
            return

        logger.info(f"Discovering plugins in: {plugin_dir}")
        for py_file in plugin_dir.glob("*_plugin.py"):
            await self.load_plugin(py_file)

    async def load_plugin(self, path: Path) -> None:
        """Load a plugin from file.

        Args:
            path: Path to plugin file
        """
        try:
            logger.debug(f"Loading plugin from: {path}")
            spec = importlib.util.spec_from_file_location(f"plugin_{path.stem}", path)
            if not spec or not spec.loader:
                logger.warning(f"Could not load spec for: {path}")
                return

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Find ProviderPlugin implementations
            for attr_name in dir(module):
                if attr_name.startswith("_"):
                    continue

                attr = getattr(module, attr_name)
                if not isinstance(attr, type):
                    continue

                # Check if it's a ProviderPlugin implementation
                # Use hasattr checks instead of issubclass for protocol
                try:
                    if (
                        hasattr(attr, "name")
                        and hasattr(attr, "version")
                        and hasattr(attr, "create_adapter")
                        and hasattr(attr, "create_config")
                        and hasattr(attr, "validate")
                        and callable(getattr(attr, "create_adapter", None))
                        and callable(getattr(attr, "create_config", None))
                        and callable(getattr(attr, "validate", None))
                    ):
                        plugin = attr()
                        await self.register(plugin)
                except (TypeError, AttributeError):
                    # Not a valid plugin class
                    continue

        except Exception as e:
            logger.error(f"Failed to load plugin from {path}: {e}")

    async def register(self, plugin: ProviderPlugin) -> None:
        """Register a plugin.

        Args:
            plugin: Plugin to register
        """
        try:
            # Validate plugin
            if not await plugin.validate():
                logger.warning(f"Plugin {plugin.name} failed validation")
                return

            # Create adapter
            adapter = plugin.create_adapter()

            # Register plugin and adapter
            self._plugins[plugin.name] = plugin
            self._adapters[plugin.name] = adapter

            logger.info(f"Registered plugin: {plugin.name} v{plugin.version}")

        except Exception as e:
            logger.error(f"Failed to register plugin {plugin.name}: {e}")

    def get_adapter(self, name: str) -> BaseAdapter | None:
        """Get adapter for provider.

        Args:
            name: Provider name

        Returns:
            Adapter instance or None
        """
        return self._adapters.get(name)

    def get_plugin(self, name: str) -> ProviderPlugin | None:
        """Get plugin by name.

        Args:
            name: Plugin name

        Returns:
            Plugin instance or None
        """
        return self._plugins.get(name)

    def list_plugins(self) -> list[str]:
        """List registered plugins.

        Returns:
            List of plugin names
        """
        return list(self._plugins.keys())

    def list_adapters(self) -> list[str]:
        """List registered adapters.

        Returns:
            List of adapter names
        """
        return list(self._adapters.keys())

    async def unregister(self, name: str) -> bool:
        """Unregister a plugin.

        Args:
            name: Plugin name

        Returns:
            True if unregistered, False if not found
        """
        if name in self._plugins:
            del self._plugins[name]
            if name in self._adapters:
                del self._adapters[name]
            logger.info(f"Unregistered plugin: {name}")
            return True
        return False

    async def reload_plugin(self, name: str, path: Path) -> bool:
        """Reload a specific plugin.

        Args:
            name: Plugin name
            path: Path to plugin file

        Returns:
            True if reloaded successfully
        """
        # Unregister existing
        await self.unregister(name)

        # Load from path
        await self.load_plugin(path)

        # Check if registered
        return name in self._plugins
