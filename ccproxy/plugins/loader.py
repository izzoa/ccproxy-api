"""Plugin discovery and loading mechanism."""

import importlib.metadata
import importlib.util
import subprocess
from pathlib import Path

import structlog

from ccproxy.plugins.protocol import ProviderPlugin


logger = structlog.get_logger(__name__)


class PluginLoader:
    """Handles plugin discovery and loading."""

    def _check_plugin_dependencies(self, plugin_dir: Path) -> bool:
        """Check if plugin dependencies are installed.

        Args:
            plugin_dir: Path to the plugin directory

        Returns:
            bool: True if dependencies are satisfied or installed successfully
        """
        pyproject_path = plugin_dir / "pyproject.toml"
        if not pyproject_path.exists():
            # No pyproject.toml, assume plugin has no extra dependencies
            return True

        try:
            # Parse pyproject.toml to check dependencies
            import tomllib

            with pyproject_path.open("rb") as f:
                data = tomllib.load(f)

            dependencies = data.get("project", {}).get("dependencies", [])
            if not dependencies:
                return True

            # Check if dependencies are installed
            missing = []
            for dep in dependencies:
                # Extract package name from dependency spec
                pkg_name = (
                    dep.split(">=")[0]
                    .split("==")[0]
                    .split("<")[0]
                    .split(">")[0]
                    .strip()
                )
                try:
                    importlib.metadata.version(pkg_name)
                except importlib.metadata.PackageNotFoundError:
                    missing.append(dep)

            if missing:
                logger.warning(
                    f"Plugin {plugin_dir.name} has missing dependencies: {missing}. "
                    "Consider running 'uv sync' to install all workspace dependencies."
                )
                # Optionally attempt to install missing dependencies
                # This is disabled by default for security/stability
                # return self._install_plugin_dependencies(plugin_dir)
                return False

            return True

        except Exception as e:
            logger.error(f"Failed to check dependencies for {plugin_dir}: {e}")
            return True  # Continue loading even if check fails

    def _install_plugin_dependencies(self, plugin_dir: Path) -> bool:
        """Install plugin dependencies using uv.

        Args:
            plugin_dir: Path to the plugin directory

        Returns:
            bool: True if installation succeeded
        """
        try:
            logger.info(f"Installing dependencies for plugin {plugin_dir.name}")
            result = subprocess.run(
                ["uv", "pip", "install", "-e", str(plugin_dir)],
                capture_output=True,
                text=True,
                check=True,
            )
            logger.info(f"Successfully installed dependencies for {plugin_dir.name}")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(
                f"Failed to install dependencies for {plugin_dir.name}: {e.stderr}"
            )
            return False
        except FileNotFoundError:
            logger.error("uv command not found. Please install uv.")
            return False

    async def discover_plugins(self) -> list[ProviderPlugin]:
        """Discover plugins from multiple sources.

        Returns:
            list: List of discovered plugin instances (duplicates removed)
        """
        plugins = []

        # Load from entry points (for installed packages)
        plugins.extend(self._load_from_entry_points())

        # Load from plugins directory (for development)
        plugins.extend(self._load_from_directory())

        # Remove duplicates based on plugin name
        seen_names = set()
        unique_plugins = []
        for plugin in plugins:
            if plugin.name not in seen_names:
                unique_plugins.append(plugin)
                seen_names.add(plugin.name)
            else:
                logger.debug(
                    f"Duplicate plugin name found: {plugin.name} (directory version will be used)"
                )

        return unique_plugins

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
                    logger.debug("plugin_loaded", plugin=entry_point.name)
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

        # Try multiple possible locations for plugins directory
        possible_locations = [
            # Development: plugins at repository root
            Path(__file__).parent.parent.parent / "plugins",
            # Installed: plugins as a top-level package
            Path(__file__).parent.parent / "plugins",
            # Alternative installed location
            Path(__file__).parent / "plugins",
        ]

        plugin_dir = None
        for location in possible_locations:
            if location.exists() and location.is_dir():
                plugin_dir = location
                logger.debug(f"Found plugin directory at: {plugin_dir}")
                break

        if not plugin_dir:
            logger.debug("Plugin directory not found in any expected location")
            return plugins

        for subdir in plugin_dir.iterdir():
            if not subdir.is_dir() or subdir.name.startswith("_"):
                continue

            # Check plugin dependencies if pyproject.toml exists
            if not self._check_plugin_dependencies(subdir):
                logger.warning(
                    f"Skipping plugin {subdir.name} due to missing dependencies"
                )
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
                        logger.debug(f"Loaded plugin from directory: {subdir.name}")
                    else:
                        logger.warning(f"No Plugin class found in {subdir}/plugin.py")
            except Exception as e:
                logger.error(f"Failed to load plugin from {subdir}: {e}", exc_info=e)

        return plugins

    def load_plugins_with_paths(self) -> list[tuple[ProviderPlugin, Path | None]]:
        """Load plugins and track their file paths.

        Returns:
            list: List of (plugin instance, plugin path) tuples
        """
        plugins_with_paths: list[tuple[ProviderPlugin, Path | None]] = []

        # Load from entry points (no paths)
        for plugin in self._load_from_entry_points():
            plugins_with_paths.append((plugin, None))

        # Load from directory with paths
        possible_locations = [
            Path(__file__).parent.parent.parent / "plugins",
            Path(__file__).parent.parent / "plugins",
            Path(__file__).parent / "plugins",
        ]

        plugin_dir = None
        for location in possible_locations:
            if location.exists() and location.is_dir():
                plugin_dir = location
                break

        if plugin_dir:
            for subdir in plugin_dir.iterdir():
                if not subdir.is_dir() or subdir.name.startswith("_"):
                    continue

                # Check plugin dependencies if pyproject.toml exists
                if not self._check_plugin_dependencies(subdir):
                    logger.warning(
                        f"Skipping plugin {subdir.name} due to missing dependencies"
                    )
                    continue

                plugin_file = subdir / "plugin.py"
                try:
                    spec = importlib.util.spec_from_file_location(
                        f"plugins.{subdir.name}.plugin", plugin_file
                    )
                    if spec and spec.loader:
                        module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(module)

                        if hasattr(module, "Plugin"):
                            plugin_instance = module.Plugin()
                            plugins_with_paths.append((plugin_instance, plugin_file))
                            logger.info(f"Loaded plugin from {plugin_file}")
                except Exception as e:
                    logger.error(f"Failed to load plugin from {plugin_file}: {e}")

        # Remove duplicates based on plugin name, keeping the one with a path
        seen_names = {}
        unique_plugins: list[tuple[ProviderPlugin, Path | None]] = []
        for plugin, path in plugins_with_paths:
            if plugin.name not in seen_names:
                seen_names[plugin.name] = (plugin, path)
            elif path is not None and seen_names[plugin.name][1] is None:
                # Prefer the one with a path
                seen_names[plugin.name] = (plugin, path)

        return list(seen_names.values())

    def load_single_plugin(self, plugin_dir: Path) -> ProviderPlugin | None:
        """Load a single plugin from its directory.

        Args:
            plugin_dir: Path to the plugin directory

        Returns:
            Plugin instance or None if loading failed
        """
        # Check plugin dependencies if pyproject.toml exists
        if not self._check_plugin_dependencies(plugin_dir):
            logger.warning(
                f"Cannot load plugin {plugin_dir.name} due to missing dependencies"
            )
            return None

        try:
            plugin_file = plugin_dir / "plugin.py"
            if not plugin_file.exists():
                logger.error(f"Plugin file not found: {plugin_file}")
                return None

            spec = importlib.util.spec_from_file_location(
                f"plugins.{plugin_dir.name}.plugin", plugin_file
            )
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                if hasattr(module, "Plugin"):
                    plugin_instance = module.Plugin()
                    logger.info(f"Loaded single plugin from {plugin_file}")
                    if isinstance(plugin_instance, ProviderPlugin):
                        return plugin_instance
                    else:
                        logger.error(
                            f"Plugin from {plugin_file} is not a ProviderPlugin"
                        )
                        return None
                else:
                    logger.error(f"No Plugin class found in {plugin_file}")
        except Exception as e:
            logger.error(f"Failed to load plugin from {plugin_dir}: {e}")

        return None

    async def discover_all_plugins(self) -> list[ProviderPlugin]:
        """Discover all plugins from all sources.

        This method is kept for backward compatibility but simply delegates
        to discover_plugins() which now handles all plugin sources.

        Returns:
            list: Complete list of discovered plugin instances
        """
        return await self.discover_plugins()
