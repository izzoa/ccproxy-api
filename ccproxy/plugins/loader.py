"""Plugin discovery and loading mechanism."""

import importlib.metadata
import importlib.util
from pathlib import Path
from typing import Any

import structlog

from ccproxy.plugins.dependency_resolver import PluginDependencyResolver
from ccproxy.plugins.protocol import ProviderPlugin


logger = structlog.get_logger(__name__)


class PluginLoader:
    """Handles plugin discovery and loading."""

    def __init__(self, auto_install: bool = False, require_user_consent: bool = True):
        """Initialize plugin loader.

        Args:
            auto_install: Whether to automatically install missing dependencies
            require_user_consent: Whether to require user consent before installing
        """
        self.dependency_resolver = PluginDependencyResolver(
            auto_install=auto_install, require_user_consent=require_user_consent
        )

    def _check_plugin_dependencies(self, plugin_dir: Path) -> bool:
        """Check if plugin dependencies are installed using the dependency resolver.

        Args:
            plugin_dir: Path to the plugin directory

        Returns:
            bool: True if dependencies are satisfied
        """
        result = self.dependency_resolver.analyze_plugin_dependencies(plugin_dir)

        if result.error:
            logger.error(
                f"Error analyzing dependencies for {plugin_dir.name}: {result.error}"
            )
            # Continue loading even if analysis fails to maintain backward compatibility
            return True

        if not result.all_satisfied:
            missing_deps = [dep.name for dep in result.missing_dependencies]
            logger.warning(
                f"Plugin {plugin_dir.name} has unsatisfied dependencies",
                missing_dependencies=missing_deps,
                suggestion="Run 'uv sync' to install all workspace dependencies or enable auto_install",
            )
            return False

        if result.dependencies:
            logger.debug(
                f"All dependencies satisfied for plugin {plugin_dir.name}",
                dependencies=[dep.name for dep in result.installed_dependencies],
            )

        return True

    async def resolve_plugin_dependencies(
        self, plugin_dir: Path, user_consent_callback: Any = None
    ) -> bool:
        """Resolve missing dependencies for a plugin.

        Args:
            plugin_dir: Path to the plugin directory
            user_consent_callback: Optional callback to get user consent

        Returns:
            True if all dependencies are resolved
        """
        result = self.dependency_resolver.analyze_plugin_dependencies(plugin_dir)
        return await self.dependency_resolver.resolve_dependencies(
            result, user_consent_callback
        )

    def get_dependency_report(self, plugin_dirs: list[Path]) -> dict[str, Any]:
        """Generate a comprehensive dependency report for multiple plugins.

        Args:
            plugin_dirs: List of plugin directories to analyze

        Returns:
            Dictionary with dependency report
        """
        results = []
        for plugin_dir in plugin_dirs:
            result = self.dependency_resolver.analyze_plugin_dependencies(plugin_dir)
            results.append(result)

        return self.dependency_resolver.generate_dependency_report(results)

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
                except ModuleNotFoundError as e:
                    logger.error(
                        "plugin_entry_point_module_not_found",
                        plugin=entry_point.name,
                        error=str(e),
                        exc_info=e,
                    )
                except ImportError as e:
                    logger.error(
                        "plugin_entry_point_import_failed",
                        plugin=entry_point.name,
                        error=str(e),
                        exc_info=e,
                    )
                except AttributeError as e:
                    logger.error(
                        "plugin_entry_point_missing_class",
                        plugin=entry_point.name,
                        error=str(e),
                        exc_info=e,
                    )
                except Exception as e:
                    logger.error(
                        "unexpected_plugin_entry_point_error",
                        plugin=entry_point.name,
                        error=str(e),
                        exc_info=e,
                    )
        except (ModuleNotFoundError, ImportError) as e:
            logger.debug("no_entry_points_found", error=str(e), exc_info=e)
        except Exception as e:
            logger.debug("unexpected_entry_points_error", error=str(e), exc_info=e)
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
            except FileNotFoundError as e:
                logger.error(
                    "plugin_file_not_found",
                    plugin=subdir.name,
                    error=str(e),
                    exc_info=e,
                )
            except ModuleNotFoundError as e:
                logger.error(
                    "plugin_module_not_found",
                    plugin=subdir.name,
                    error=str(e),
                    exc_info=e,
                )
            except ImportError as e:
                logger.error(
                    "plugin_import_failed", plugin=subdir.name, error=str(e), exc_info=e
                )
            except AttributeError as e:
                logger.error(
                    "plugin_missing_class", plugin=subdir.name, error=str(e), exc_info=e
                )
            except Exception as e:
                logger.error(
                    "unexpected_plugin_error",
                    plugin=subdir.name,
                    error=str(e),
                    exc_info=e,
                )

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
                except FileNotFoundError as e:
                    logger.error(
                        "plugin_file_not_found",
                        plugin=subdir.name,
                        plugin_file=str(plugin_file),
                        error=str(e),
                        exc_info=e,
                    )
                except ModuleNotFoundError as e:
                    logger.error(
                        "plugin_module_not_found",
                        plugin=subdir.name,
                        plugin_file=str(plugin_file),
                        error=str(e),
                        exc_info=e,
                    )
                except ImportError as e:
                    logger.error(
                        "plugin_import_failed",
                        plugin=subdir.name,
                        plugin_file=str(plugin_file),
                        error=str(e),
                        exc_info=e,
                    )
                except AttributeError as e:
                    logger.error(
                        "plugin_missing_class",
                        plugin=subdir.name,
                        plugin_file=str(plugin_file),
                        error=str(e),
                        exc_info=e,
                    )
                except Exception as e:
                    logger.error(
                        "unexpected_plugin_error",
                        plugin=subdir.name,
                        plugin_file=str(plugin_file),
                        error=str(e),
                        exc_info=e,
                    )

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
                logger.error(
                    "plugin_file_not_found",
                    plugin=plugin_dir.name,
                    plugin_file=str(plugin_file),
                )
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
                            "plugin_not_provider_plugin",
                            plugin=plugin_dir.name,
                            plugin_file=str(plugin_file),
                            plugin_type=type(plugin_instance).__name__,
                        )
                        return None
                else:
                    logger.error(
                        "plugin_missing_class",
                        plugin=plugin_dir.name,
                        plugin_file=str(plugin_file),
                    )
        except FileNotFoundError as e:
            logger.error(
                "plugin_file_not_found",
                plugin=plugin_dir.name,
                error=str(e),
                exc_info=e,
            )
        except ModuleNotFoundError as e:
            logger.error(
                "plugin_module_not_found",
                plugin=plugin_dir.name,
                error=str(e),
                exc_info=e,
            )
        except ImportError as e:
            logger.error(
                "plugin_import_failed", plugin=plugin_dir.name, error=str(e), exc_info=e
            )
        except AttributeError as e:
            logger.error(
                "plugin_missing_class", plugin=plugin_dir.name, error=str(e), exc_info=e
            )
        except Exception as e:
            logger.error(
                "unexpected_plugin_error",
                plugin=plugin_dir.name,
                error=str(e),
                exc_info=e,
            )

        return None

    async def discover_all_plugins(self) -> list[ProviderPlugin]:
        """Discover all plugins from all sources.

        This method is kept for backward compatibility but simply delegates
        to discover_plugins() which now handles all plugin sources.

        Returns:
            list: Complete list of discovered plugin instances
        """
        return await self.discover_plugins()
