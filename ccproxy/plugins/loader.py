"""Plugin discovery and loading mechanism using entry points."""

import importlib.metadata

import structlog

from ccproxy.plugins.protocol import ProviderPlugin


logger = structlog.get_logger(__name__)


class PluginLoader:
    """Handles plugin discovery and loading via entry points."""

    def __init__(self) -> None:
        """Initialize plugin loader."""
        pass

    async def load_plugins(self) -> list[ProviderPlugin]:
        """Load plugins using importlib.metadata entry points.

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
