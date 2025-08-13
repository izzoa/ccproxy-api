"""Plugin discovery and loading mechanism using entry points."""

import importlib.metadata

import structlog

from ccproxy.plugins.protocol import BasePlugin


logger = structlog.get_logger(__name__)


class PluginLoader:
    """Handles plugin discovery and loading via entry points."""

    def __init__(self) -> None:
        """Initialize plugin loader."""
        pass

    async def load_plugins(self) -> list[BasePlugin]:
        """Load plugins using importlib.metadata entry points.

        Plugins should be registered via setuptools entry points in pyproject.toml:

        [project.entry-points."ccproxy.plugins"]
        claude_api = "plugins.claude_api.plugin:Plugin"
        claude_sdk = "plugins.claude_sdk.plugin:Plugin"
        codex = "plugins.codex.plugin:Plugin"

        [project.entry-points."ccproxy.system_plugins"]
        prometheus = "plugins.observability_prometheus.plugin:PrometheusPlugin"
        analytics = "plugins.observability_analytics.plugin:AnalyticsPlugin"

        Returns:
            list: List of discovered plugin instances in dependency order
        """
        plugins = self._load_from_entry_points()
        return self._resolve_plugin_dependencies(plugins)

    def _load_from_entry_points(self) -> list[BasePlugin]:
        """Load plugins registered via setuptools entry points.

        Returns:
            list: List of plugin instances from entry points
        """
        plugins: list[BasePlugin] = []
        loaded_names: set[str] = set()

        # Load provider plugins
        for plugin in self._load_entry_point_group("ccproxy.plugins", "provider"):
            if plugin.name not in loaded_names:
                plugins.append(plugin)
                loaded_names.add(plugin.name)

        # Load system plugins
        for plugin in self._load_entry_point_group("ccproxy.system_plugins", "system"):
            if plugin.name not in loaded_names:
                plugins.append(plugin)
                loaded_names.add(plugin.name)

        return plugins

    def _load_entry_point_group(self, group: str, plugin_type: str) -> list[BasePlugin]:
        """Load plugins from a specific entry point group."""
        plugins: list[BasePlugin] = []
        try:
            for entry_point in importlib.metadata.entry_points(group=group):
                try:
                    plugin_class = entry_point.load()
                    plugins.append(plugin_class())
                    logger.debug(
                        "plugin_loaded", plugin=entry_point.name, type=plugin_type
                    )
                except ModuleNotFoundError as e:
                    logger.error(
                        "plugin_entry_point_module_not_found",
                        plugin=entry_point.name,
                        type=plugin_type,
                        error=str(e),
                        exc_info=e,
                    )
                except ImportError as e:
                    logger.error(
                        "plugin_entry_point_import_failed",
                        plugin=entry_point.name,
                        type=plugin_type,
                        error=str(e),
                        exc_info=e,
                    )
                except AttributeError as e:
                    logger.error(
                        "plugin_entry_point_missing_class",
                        plugin=entry_point.name,
                        type=plugin_type,
                        error=str(e),
                        exc_info=e,
                    )
                except Exception as e:
                    logger.error(
                        "unexpected_plugin_entry_point_error",
                        plugin=entry_point.name,
                        type=plugin_type,
                        error=str(e),
                        exc_info=e,
                    )
        except (ModuleNotFoundError, ImportError) as e:
            logger.debug("no_entry_points_found", group=group, error=str(e), exc_info=e)
        except Exception as e:
            logger.debug(
                "unexpected_entry_points_error", group=group, error=str(e), exc_info=e
            )
        return plugins

    def _resolve_plugin_dependencies(
        self, plugins: list[BasePlugin]
    ) -> list[BasePlugin]:
        """Resolve plugin dependencies and return in correct load order.

        Uses topological sort to ensure plugins are loaded after their dependencies.

        Args:
            plugins: List of plugin instances

        Returns:
            List of plugins in dependency order

        Raises:
            ValueError: If circular dependencies are detected
        """
        if not plugins:
            return plugins

        plugin_map = {plugin.name: plugin for plugin in plugins}
        ordered = []
        visited = set()
        visiting = set()  # For cycle detection

        def visit(plugin_name: str) -> None:
            if plugin_name in visiting:
                raise ValueError(
                    f"Circular dependency detected involving plugin: {plugin_name}"
                )
            if plugin_name in visited:
                return

            plugin = plugin_map.get(plugin_name)
            if not plugin:
                logger.warning("plugin_dependency_not_found", plugin=plugin_name)
                return

            visiting.add(plugin_name)

            # Visit dependencies first (handle plugins without dependencies property)
            dependencies = getattr(plugin, "dependencies", None) or []
            for dep_name in dependencies:
                visit(dep_name)

            visiting.remove(plugin_name)
            visited.add(plugin_name)
            ordered.append(plugin)

        # Visit all plugins
        for plugin in plugins:
            visit(plugin.name)

        logger.debug("plugin_load_order", order=[p.name for p in ordered])
        return ordered
