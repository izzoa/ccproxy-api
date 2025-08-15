"""Plugin discovery and loading mechanism."""

import importlib.metadata
import importlib.util
from pathlib import Path

import structlog

from ccproxy.plugins.protocol import ProviderPlugin


logger = structlog.get_logger(__name__)


class PluginLoader:
    """Handles plugin discovery and loading."""

    async def discover_plugins(self) -> list[ProviderPlugin]:
        """Discover plugins from multiple sources.

        Returns:
            list: List of discovered plugin instances
        """
        plugins = []

        # Load from entry points (for installed packages)
        plugins.extend(self._load_from_entry_points())

        # Load from plugins directory (for development)
        plugins.extend(self._load_from_directory())

        return plugins

    def _load_from_entry_points(self) -> list[ProviderPlugin]:
        """Load plugins registered via setuptools entry points.

        Returns:
            list: List of plugin instances from entry points
        """
        plugins: list[ProviderPlugin] = []
        try:
            for entry_point in importlib.metadata.entry_points(group="ccproxy.plugins"):
                try:
                    plugin_class = entry_point.load()
                    plugins.append(plugin_class())
                    logger.info(f"Loaded plugin from entry point: {entry_point.name}")
                except Exception as e:
                    logger.error(f"Failed to load plugin {entry_point.name}: {e}")
        except Exception as e:
            logger.debug(f"No entry points found or error accessing them: {e}")
        return plugins

    def _load_from_directory(self) -> list[ProviderPlugin]:
        """Load plugins from the plugins/ directory.

        Returns:
            list: List of plugin instances from directory
        """
        plugins: list[ProviderPlugin] = []
        plugin_dir = Path(__file__).parent.parent.parent / "plugins"

        if not plugin_dir.exists():
            logger.debug(f"Plugin directory does not exist: {plugin_dir}")
            return plugins

        for subdir in plugin_dir.iterdir():
            if not subdir.is_dir() or subdir.name.startswith("_"):
                continue

            try:
                # Import the plugin module
                spec = importlib.util.spec_from_file_location(
                    f"plugins.{subdir.name}.plugin", subdir / "plugin.py"
                )
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)

                    # Look for Plugin class (standardized name)
                    if hasattr(module, "Plugin"):
                        plugin_instance = module.Plugin()
                        plugins.append(plugin_instance)
                        logger.info(f"Loaded plugin from directory: {subdir.name}")
                    else:
                        logger.warning(f"No Plugin class found in {subdir}/plugin.py")
            except Exception as e:
                logger.error(f"Failed to load plugin from {subdir}: {e}")

        return plugins

    def _load_legacy_plugins(self) -> list[ProviderPlugin]:
        """Load legacy plugins from plugins/ root directory.

        Returns:
            list: List of legacy plugin instances
        """
        plugins: list[ProviderPlugin] = []
        plugin_dir = Path(__file__).parent.parent.parent / "plugins"

        if not plugin_dir.exists():
            return plugins

        # Load legacy plugins with _plugin.py suffix
        for py_file in plugin_dir.glob("*_plugin.py"):
            try:
                spec = importlib.util.spec_from_file_location(
                    f"plugin_{py_file.stem}", py_file
                )
                if not spec or not spec.loader:
                    logger.warning(f"Could not load spec for: {py_file}")
                    continue

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
                    try:
                        if self._is_valid_plugin_class(attr):
                            plugin = attr()
                            plugins.append(plugin)
                            logger.info(f"Loaded legacy plugin: {py_file.name}")
                            break
                    except (TypeError, AttributeError):
                        continue

            except Exception as e:
                logger.error(f"Failed to load legacy plugin from {py_file}: {e}")

        return plugins

    def _is_valid_plugin_class(self, cls: type) -> bool:
        """Check if a class implements the ProviderPlugin protocol.

        Args:
            cls: Class to check

        Returns:
            bool: True if class implements ProviderPlugin protocol
        """
        required_methods = [
            "name",
            "version",
            "create_adapter",
            "create_config",
            "validate",
        ]
        required_new_methods = [
            "router_prefix",
            "initialize",
            "shutdown",
            "get_routes",
            "health_check",
        ]

        # Check for basic required methods (legacy compatibility)
        for method in required_methods:
            if not hasattr(cls, method):
                return False

        # Check if it's an enhanced plugin with new methods
        has_enhanced_methods = all(
            hasattr(cls, method) for method in required_new_methods
        )

        # Accept both legacy and enhanced plugins
        return True

    async def discover_all_plugins(self) -> list[ProviderPlugin]:
        """Discover all plugins from all sources.

        Returns:
            list: Complete list of discovered plugin instances
        """
        plugins = []

        # Load enhanced plugins first
        plugins.extend(await self.discover_plugins())

        # Load legacy plugins as fallback
        plugins.extend(self._load_legacy_plugins())

        # Remove duplicates based on plugin name
        seen_names = set()
        unique_plugins = []
        for plugin in plugins:
            if plugin.name not in seen_names:
                unique_plugins.append(plugin)
                seen_names.add(plugin.name)
            else:
                logger.warning(f"Duplicate plugin name found: {plugin.name}")

        return unique_plugins
