"""Plugin discovery and loading mechanism using entry points."""

import importlib.metadata
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

    async def resolve_plugin_dependencies(
        self, plugin_dir: Path, user_consent_callback: Any = None
    ) -> bool:
        """[DEPRECATED] Resolve missing dependencies for a plugin.

        Dependencies should be managed at the package level via pyproject.toml.

        Args:
            plugin_dir: Path to the plugin directory
            user_consent_callback: Optional callback to get user consent

        Returns:
            True - method is deprecated
        """
        logger.warning(
            "resolve_plugin_dependencies_deprecated",
            message="Dependencies should be managed via pyproject.toml",
        )
        return True

    def get_dependency_report(self, plugin_dirs: list[Path]) -> dict[str, Any]:
        """[DEPRECATED] Generate a dependency report.

        Dependencies should be managed at the package level via pyproject.toml.

        Args:
            plugin_dirs: List of plugin directories to analyze

        Returns:
            Empty dict - method is deprecated
        """
        logger.warning(
            "get_dependency_report_deprecated",
            message="Dependencies should be managed via pyproject.toml",
        )
        return {}

    async def discover_plugins(self) -> list[ProviderPlugin]:
        """Discover plugins using importlib.metadata entry points.

        This is the simplified and standardized way to discover plugins.
        Plugins should be registered via setuptools entry points in pyproject.toml:

        [project.entry-points."ccproxy.plugins"]
        claude_api = "plugins.claude_api.plugin:Plugin"
        claude_sdk = "plugins.claude_sdk.plugin:Plugin"
        codex = "plugins.codex.plugin:Plugin"

        Returns:
            list: List of discovered plugin instances
        """
        return self._load_from_entry_points()

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

    def load_plugins_with_paths(self) -> list[tuple[ProviderPlugin, Path | None]]:
        """Load plugins via entry points.

        Note: Entry point plugins don't have direct file paths since they're
        loaded from installed packages. This method returns None for paths.

        Returns:
            list: List of (plugin instance, None) tuples
        """
        plugins_with_paths: list[tuple[ProviderPlugin, Path | None]] = []

        # Load from entry points (paths not available for installed packages)
        for plugin in self._load_from_entry_points():
            plugins_with_paths.append((plugin, None))

        return plugins_with_paths

    def load_single_plugin(self, plugin_dir: Path) -> ProviderPlugin | None:
        """[DEPRECATED] Load a single plugin from its directory.

        This method is deprecated. Plugins should be installed as packages
        and discovered via entry points.

        Args:
            plugin_dir: Path to the plugin directory

        Returns:
            None - this method is deprecated
        """
        logger.warning(
            "load_single_plugin_deprecated",
            plugin_dir=str(plugin_dir),
            message="Use entry points for plugin discovery instead",
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
