"""Plugin discovery system for finding and loading plugins.

This module provides mechanisms to discover plugins from the filesystem
and dynamically load their factories.
"""

import importlib
import importlib.util
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

import structlog

from ccproxy.config import Settings


try:
    # Python 3.10+
    from importlib.metadata import EntryPoint, entry_points
except ImportError:  # pragma: no cover
    entry_points = None  # type: ignore[assignment]
    EntryPoint = Any  # type: ignore[misc,assignment]

from .interfaces import PluginFactory


logger = structlog.get_logger(__name__)


def _get_logger(context: str, plugin_name: str | None = None) -> Any:
    """Return a structlog logger bound with shared plugin metadata."""

    bound = logger.bind(type=context, category="plugin")
    if plugin_name:
        bound = bound.bind(name=plugin_name)
    return bound


def _log_missing_dependency(
    *, plugin_name: str, error: ModuleNotFoundError, context: str
) -> None:
    """Log a structured warning for a missing plugin dependency."""

    missing_dependency = getattr(error, "name", None)
    if not missing_dependency:
        missing_dependency = str(error).removeprefix("No module named ").strip("'\"")

    event_name = "plugin_dependency_missing"
    log_payload = {"dependency": missing_dependency, "details": context}

    _get_logger(context=context, plugin_name=plugin_name).warning(
        event_name,
        **log_payload,
    )

    logging.warning("%s %s", event_name, log_payload)


class PluginDiscovery:
    """Discovers and loads plugins from the filesystem."""

    def __init__(self, plugins_dirs: Iterable[Path]):
        """Initialize plugin discovery.

        Args:
            plugins_dirs: Ordered directories containing plugin packages
        """
        seen: set[Path] = set()
        ordered: list[Path] = []
        for directory in plugins_dirs:
            path = Path(directory)
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            ordered.append(path)
        self.plugin_dirs = ordered
        self.discovered_plugins: dict[str, Path] = {}

    def discover_plugins(self) -> dict[str, Path]:
        """Discover all plugins in the plugins directory.

        Returns:
            Dictionary mapping plugin names to their paths
        """
        self.discovered_plugins.clear()

        logger_fs = _get_logger("filesystem")
        discovered: list[str] = []
        missing_dirs: list[str] = []

        for base_dir in self.plugin_dirs:
            if not base_dir.exists():
                missing_dirs.append(str(base_dir))
                continue

            for item in sorted(base_dir.iterdir()):
                if not item.is_dir() or item.name.startswith("_"):
                    continue

                plugin_file = item / "plugin.py"
                if not plugin_file.exists():
                    continue

                if item.name in self.discovered_plugins:
                    _get_logger("filesystem", item.name).debug(
                        "plugin_duplicate_ignored",
                        original=str(self.discovered_plugins[item.name]),
                        ignored=str(plugin_file),
                    )
                    continue

                self.discovered_plugins[item.name] = plugin_file
                discovered.append(item.name)

                plugin_logger = _get_logger("filesystem", item.name)
                plugin_trace = getattr(plugin_logger, "trace", plugin_logger.debug)
                plugin_trace(
                    "plugin_found",
                    path=str(plugin_file),
                )

        if missing_dirs:
            logger_fs.warning(
                "plugins_directories_missing",
                paths=missing_dirs,
            )

        # Single consolidated log for all discoveries
        logger_fs.info(
            "plugins_discovered",
            count=len(discovered),
            names=discovered if discovered else [],
            directories=[str(path) for path in self.plugin_dirs],
        )
        return self.discovered_plugins

    def load_plugin_factory(self, name: str) -> PluginFactory | None:
        """Load a plugin factory by name.

        Args:
            name: Plugin name

        Returns:
            Plugin factory or None if not found or failed to load
        """
        logger_fs = _get_logger("filesystem", name)
        if name not in self.discovered_plugins:
            logger_fs.warning("plugin_not_discovered")
            return None

        plugin_path = self.discovered_plugins[name]

        try:
            # Create module spec and load the module
            spec = importlib.util.spec_from_file_location(
                f"ccproxy.plugins.{name}.plugin", plugin_path
            )

            if not spec or not spec.loader:
                logger_fs.error("plugin_spec_creation_failed")
                return None

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Get the factory from the module
            if not hasattr(module, "factory"):
                logger_fs.error(
                    "plugin_factory_not_found",
                    msg="Module must export 'factory' variable",
                )
                return None

            factory = module.factory

            if not isinstance(factory, PluginFactory):
                logger_fs.error(
                    "plugin_factory_invalid_type",
                    type=type(factory).__name__,
                )
                return None

            trace_logger = getattr(logger_fs, "trace", logger_fs.debug)
            trace_logger(
                "plugin_factory_loaded",
                version=factory.get_manifest().version,
            )

            return factory

        except ModuleNotFoundError as exc:
            _log_missing_dependency(
                plugin_name=name,
                error=exc,
                context="filesystem",
            )
            return None
        except Exception as e:
            logger_fs.error(
                "plugin_load_failed",
                error=str(e),
                exc_info=e,
            )
            return None

    def load_all_factories(
        self, plugin_filter: "PluginFilter | None" = None
    ) -> dict[str, PluginFactory]:
        """Load all discovered plugin factories.

        Returns:
            Dictionary mapping plugin names to their factories
        """
        logger_fs = _get_logger("filesystem")
        factories: dict[str, PluginFactory] = {}

        skipped_names: list[str] = []

        for name in self.discovered_plugins:
            if plugin_filter and not plugin_filter.is_enabled(name):
                skipped_names.append(name)
                continue
            factory = self.load_plugin_factory(name)
            if factory:
                factories[name] = factory

        if skipped_names:
            logger_fs.info("plugin_skipped_before_load", names=skipped_names)

        logger_fs.info(
            "plugin_factories_loaded",
            count=len(factories),
            names=list(factories.keys()),
        )

        return factories

    def load_entry_point_factories(
        self,
        skip_names: set[str] | None = None,
        plugin_filter: "PluginFilter | None" = None,
    ) -> dict[str, PluginFactory]:
        """Load plugin factories from installed entry points.

        Returns:
            Dictionary mapping plugin names to their factories
        """
        factories: dict[str, PluginFactory] = {}
        logger_ep = _get_logger("entrypoint")
        if entry_points is None:
            logger_ep.debug("entry_points_not_available")
            return factories

        try:
            groups = entry_points()
            eps = []
            # importlib.metadata API differences across Python versions
            if hasattr(groups, "select"):
                eps = list(groups.select(group="ccproxy.plugins"))
            else:  # pragma: no cover
                eps = list(groups.get("ccproxy.plugins", []))

            skip_logged: set[str] = set()
            filtered_skipped: list[str] = []
            for ep in eps:
                name = ep.name
                # Skip entry points that collide with existing filesystem plugins
                if skip_names and name in skip_names:
                    if name not in skip_logged:
                        _get_logger("entrypoint", name).debug(
                            "entry_point_skipped_preexisting_filesystem"
                        )
                        skip_logged.add(name)
                    continue
                # Skip duplicates within entry points themselves
                if name in factories:
                    if name not in skip_logged:
                        _get_logger("entrypoint", name).debug(
                            "entry_point_duplicate_ignored"
                        )
                        skip_logged.add(name)
                    continue
                if plugin_filter and not plugin_filter.is_enabled(name):
                    filtered_skipped.append(name)
                    continue
                try:
                    # Primary load
                    obj = ep.load()
                except ModuleNotFoundError as exc:
                    _log_missing_dependency(
                        plugin_name=name,
                        error=exc,
                        context="entrypoint",
                    )
                    continue
                except Exception as e:
                    # Fallback: import module and get 'factory'
                    try:
                        module_name = getattr(ep, "module", None)
                        if not module_name:
                            value = getattr(ep, "value", "")
                            module_name = value.split(":")[0] if ":" in value else None
                        if not module_name:
                            raise e
                        mod = importlib.import_module(module_name)
                        if hasattr(mod, "factory"):
                            obj = mod.factory
                        else:
                            raise e
                    except ModuleNotFoundError as exc2:
                        _log_missing_dependency(
                            plugin_name=name,
                            error=exc2,
                            context="entrypoint_fallback",
                        )
                        continue
                    except Exception as e2:
                        _get_logger("entrypoint", name).error(
                            "entry_point_load_failed", error=str(e2), exc_info=e2
                        )
                        continue

                factory: PluginFactory | None = None

                # If the object already looks like a factory (duck typing)
                if hasattr(obj, "get_manifest") and hasattr(obj, "create_runtime"):
                    factory = cast(PluginFactory, obj)
                # If it's callable, try to call to get a factory
                elif callable(obj):
                    try:
                        maybe = obj()
                        if hasattr(maybe, "get_manifest") and hasattr(
                            maybe, "create_runtime"
                        ):
                            factory = cast(PluginFactory, maybe)
                    except Exception:
                        factory = None

                if not factory:
                    _get_logger("entrypoint", name).warning(
                        "entry_point_not_factory", obj_type=type(obj).__name__
                    )
                    continue

                factories[name] = factory
                # logger.debug(
                #     "entry_point_factory_loaded",
                #     name=name,
                #     version=factory.get_manifest().version,
                #     category="plugin",
                # )

            if filtered_skipped:
                logger_ep.info("plugin_skipped_before_load", names=filtered_skipped)
        except Exception as e:  # pragma: no cover
            logger_ep.error("entry_points_enumeration_failed", error=str(e), exc_info=e)
        return factories


class PluginFilter:
    """Filter plugins based on configuration."""

    def __init__(
        self,
        enabled_plugins: list[str] | None = None,
        disabled_plugins: list[str] | None = None,
        settings: Any | None = None,
    ):
        """Initialize plugin filter.

        Args:
            enabled_plugins: List of explicitly enabled plugins (None = all)
            disabled_plugins: List of explicitly disabled plugins
            settings: Settings object to check individual plugin enabled flags
        """
        self.enabled_plugins = set(enabled_plugins) if enabled_plugins else None
        self.disabled_plugins = set(disabled_plugins) if disabled_plugins else set()
        self.settings = settings

    def is_enabled(self, plugin_name: str) -> bool:
        """Check if a plugin is enabled.

        Priority hierarchy:
        1. enabled_plugins whitelist (if specified, ONLY these are enabled)
        2. disabled_plugins blacklist (blocks specific plugins)
        3. Individual plugin enabled=false setting (blocks unless overridden by enabled_plugins)

        Args:
            plugin_name: Plugin name

        Returns:
            True if plugin is enabled
        """
        # 1. If enabled_plugins is specified, ONLY those are allowed
        if self.enabled_plugins is not None:
            return plugin_name in self.enabled_plugins

        # 2. Check disabled_plugins blacklist
        if plugin_name in self.disabled_plugins:
            return False

        # 3. Check individual plugin enabled setting
        if self.settings is not None:
            try:
                # Try to get the plugin config via plugins[name]["enabled"]
                plugins_config = getattr(self.settings, "plugins", {})
                if plugin_name in plugins_config:
                    individual_config = plugins_config[plugin_name]
                    if (
                        isinstance(individual_config, dict)
                        and individual_config.get("enabled") is False
                    ):
                        return False
            except (AttributeError, TypeError):
                # If we can't check individual settings, fall back to enabled by default
                pass

        # Otherwise, enabled by default
        return True

    def filter_factories(
        self, factories: dict[str, PluginFactory]
    ) -> dict[str, PluginFactory]:
        """Filter plugin factories based on configuration.

        Args:
            factories: All discovered factories

        Returns:
            Filtered factories
        """
        logger_filter = _get_logger("filter")
        filtered = {}

        for name, factory in factories.items():
            if self.is_enabled(name):
                filtered[name] = factory
            else:
                _get_logger("filter", name).info("plugin_disabled")

        return filtered


def discover_and_load_plugins(settings: Settings) -> dict[str, PluginFactory]:
    """Discover and load all configured plugins.

    Args:
        settings: Application settings

    Returns:
        Dictionary of loaded plugin factories
    """
    plugin_dirs: list[Path]
    # if len(settings.plugin_discovery.directories) > 0:
    plugin_dirs = [Path(path) for path in settings.plugin_discovery.directories]
    # else:
    # plugin_dirs = [Path(__file__).parent.parent.parent / "plugins"]

    logger_mgr = _get_logger("manager")

    logger_mgr.debug(
        "plugin_filesystem_directories",
        directories=[str(path) for path in plugin_dirs],
    )

    # Discover plugins
    discovery = PluginDiscovery(plugin_dirs)

    filter_config = PluginFilter(
        enabled_plugins=getattr(settings, "enabled_plugins", None),
        disabled_plugins=getattr(settings, "disabled_plugins", None),
        settings=settings,
    )

    # Determine whether to use local filesystem discovery
    if settings.plugins_disable_local_discovery:
        logger_mgr.info(
            "plugins_local_discovery_disabled",
            reason="settings.plugins_disable_local_discovery",
        )

    # Load entry point plugins first so filesystem plugins can override them.
    all_factories: dict[str, PluginFactory] = discovery.load_entry_point_factories(
        plugin_filter=filter_config
    )

    if not settings.plugins_disable_local_discovery:
        discovery.discover_plugins()
        filesystem_factories = discovery.load_all_factories(plugin_filter=filter_config)

        for name, factory in filesystem_factories.items():
            if name in all_factories:
                _get_logger("manager", name).debug("plugin_filesystem_override")
            all_factories[name] = factory

    filtered_factories = filter_config.filter_factories(all_factories)

    logger_mgr.info(
        "plugins_ready",
        discovered=len(all_factories),
        enabled=len(filtered_factories),
        names=list(filtered_factories.keys()),
    )

    return filtered_factories
