import importlib
from importlib import machinery
from pathlib import Path

import pytest

from ccproxy.config.settings import Settings
from ccproxy.core.plugins.declaration import PluginContext, PluginManifest
from ccproxy.core.plugins.discovery import (
    PluginDiscovery,
    PluginFilter,
    build_combined_plugin_denylist,
    discover_and_load_plugins,
)
from ccproxy.core.plugins.hooks.manager import HookManager
from ccproxy.core.plugins.interfaces import PluginFactory
from ccproxy.plugins.claude_api.config import ClaudeAPISettings
from ccproxy.plugins.claude_api.plugin import ClaudeAPIFactory
from ccproxy.services.container import ServiceContainer


class DummyFactory(PluginFactory):
    def __init__(self, name: str) -> None:
        self._manifest = PluginManifest(name=name, version="0.0.0")

    def get_manifest(self) -> PluginManifest:
        return self._manifest

    def create_runtime(self) -> object:
        return object()

    def create_context(
        self, core_services
    ) -> PluginContext:  # pragma: no cover - not used
        return PluginContext()


@pytest.mark.unit
def test_build_combined_plugin_denylist_merges_sources() -> None:
    denylist = build_combined_plugin_denylist(
        ["alpha"],
        {
            "beta": {"enabled": False},
            "gamma": {"enabled": True},
            "delta": {"enabled": None},
            "epsilon": {"enabled": True, "other": "value"},
        },
    )

    assert denylist == {"alpha", "beta"}


@pytest.mark.unit
def test_create_context_populates_default_config_when_missing() -> None:
    settings = Settings()
    container = ServiceContainer(settings)
    hook_registry = container.get_hook_registry()
    background_manager = container.get_background_hook_thread_manager()
    container.register_service(
        HookManager,
        instance=HookManager(hook_registry, background_manager),
    )
    factory = ClaudeAPIFactory()

    context = factory.create_context(container)

    assert (
        context.get(ClaudeAPISettings).model_dump() == ClaudeAPISettings().model_dump()
    )


@pytest.mark.unit
def test_load_all_factories_skips_disabled(monkeypatch):
    discovery = PluginDiscovery([Path("/does/not/matter")])
    discovery.discovered_plugins = {
        "alpha": Path("alpha/plugin.py"),
        "beta": Path("beta/plugin.py"),
    }

    loaded: list[str] = []

    def fake_load(self, name: str):
        loaded.append(name)
        return DummyFactory(name)

    monkeypatch.setattr(PluginDiscovery, "load_plugin_factory", fake_load)

    plugin_filter = PluginFilter(enabled_plugins=["alpha"], disabled_plugins=None)

    factories = discovery.load_all_factories(plugin_filter=plugin_filter)

    assert loaded == ["alpha"]
    assert list(factories.keys()) == ["alpha"]


@pytest.mark.unit
def test_load_plugin_factory_logs_missing_dependency(monkeypatch, caplog):
    discovery = PluginDiscovery([Path("/does/not/matter")])
    discovery.discovered_plugins = {"alpha": Path("alpha/plugin.py")}

    class DummyLoader:
        def create_module(self, spec):
            return None

        def exec_module(self, module):
            raise ModuleNotFoundError("sqlalchemy")

    monkeypatch.setattr(
        importlib.util,
        "spec_from_file_location",
        lambda fullname, path: machinery.ModuleSpec(fullname, DummyLoader()),
    )

    caplog.clear()
    with caplog.at_level("WARNING"):
        factory = discovery.load_plugin_factory("alpha")

    assert factory is None

    log_output = caplog.text
    assert "plugin_dependency_missing" in log_output
    assert "'dependency': 'sqlalchemy'" in log_output
    assert "'details': 'filesystem'" in log_output


@pytest.mark.unit
def test_entry_point_missing_dependency_logged(monkeypatch, caplog):
    class FakeEntryPoint:
        def __init__(self, name: str) -> None:
            self.name = name
            self.module = "ccproxy.plugins.analytics.routes"
            self.value = "ccproxy.plugins.analytics.routes:factory"

        def load(self) -> object:
            raise ModuleNotFoundError("claude_code_sdk")

    class FakeGroups:
        def select(self, group: str):
            assert group == "ccproxy.plugins"
            return [FakeEntryPoint("analytics")]

    monkeypatch.setattr(
        "ccproxy.core.plugins.discovery.entry_points",
        lambda: FakeGroups(),
    )

    discovery = PluginDiscovery([Path("/does/not/matter")])

    caplog.clear()
    with caplog.at_level("WARNING"):
        factories = discovery.load_entry_point_factories()

    assert factories == {}

    log_output = caplog.text
    assert "plugin_dependency_missing" in log_output
    assert "'dependency': 'claude_code_sdk'" in log_output
    assert "'details': 'entrypoint'" in log_output


@pytest.mark.unit
def test_load_entry_point_factories_skips_disabled(monkeypatch):
    class FakeEntryPoint:
        def __init__(self, name: str) -> None:
            self.name = name
            self.module = None
            self.value = ""

        def load(self) -> object:
            raise AssertionError("disabled plugin should not be loaded")

    class FakeGroups:
        def select(self, group: str):
            assert group == "ccproxy.plugins"
            return [FakeEntryPoint("gamma")]

    monkeypatch.setattr(
        "ccproxy.core.plugins.discovery.entry_points",
        lambda: FakeGroups(),
    )

    discovery = PluginDiscovery([Path("/does/not/matter")])
    plugin_filter = PluginFilter(enabled_plugins=None, disabled_plugins=["gamma"])

    factories = discovery.load_entry_point_factories(plugin_filter=plugin_filter)

    assert factories == {}


@pytest.mark.unit
def test_discover_and_load_plugins_respects_filters(monkeypatch):
    loaded: list[str] = []

    def fake_discover(self):
        self.discovered_plugins = {
            "alpha": Path("alpha/plugin.py"),
            "beta": Path("beta/plugin.py"),
        }
        return self.discovered_plugins

    def fake_load(self, name: str):
        loaded.append(name)
        return DummyFactory(name)

    def fake_load_entry(self, skip_names=None, plugin_filter=None):
        # No entry-point plugins for this test
        return {}

    monkeypatch.setattr(PluginDiscovery, "discover_plugins", fake_discover)
    monkeypatch.setattr(PluginDiscovery, "load_plugin_factory", fake_load)
    monkeypatch.setattr(
        PluginDiscovery,
        "load_entry_point_factories",
        fake_load_entry,
    )

    settings = Settings()
    settings.enabled_plugins = ["alpha"]
    settings.disabled_plugins = ["beta"]  # redundant but should still be respected
    settings.plugins = {"beta": {"enabled": False}}
    settings.plugins_disable_local_discovery = False

    factories = discover_and_load_plugins(settings)

    assert loaded == ["alpha"]
    assert list(factories.keys()) == ["alpha"]


@pytest.mark.unit
def test_discover_plugins_multiple_directories(tmp_path):
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()

    alpha_dir = first_dir / "alpha"
    alpha_dir.mkdir()
    (alpha_dir / "plugin.py").write_text("factory = None\n", encoding="utf-8")

    duplicate_alpha_dir = second_dir / "alpha"
    duplicate_alpha_dir.mkdir()
    (duplicate_alpha_dir / "plugin.py").write_text("factory = None\n", encoding="utf-8")

    beta_dir = second_dir / "beta"
    beta_dir.mkdir()
    (beta_dir / "plugin.py").write_text("factory = None\n", encoding="utf-8")

    discovery = PluginDiscovery([first_dir, second_dir])
    plugins = discovery.discover_plugins()

    assert set(plugins.keys()) == {"alpha", "beta"}
    assert plugins["alpha"].parent == alpha_dir


@pytest.mark.unit
def test_filesystem_plugins_override_entry_points(monkeypatch, tmp_path):
    class StubFactory(PluginFactory):
        def __init__(self, name: str, source: str) -> None:
            self._manifest = PluginManifest(name=name, version="0.1.0")
            self.source = source

        def get_manifest(self) -> PluginManifest:
            return self._manifest

        def create_runtime(self) -> object:
            return object()

        def create_context(
            self, core_services
        ) -> PluginContext:  # pragma: no cover - not used
            return PluginContext()

    entry_factory = StubFactory(name="alpha", source="entrypoint")

    class FakeEntryPoint:
        def __init__(self, name: str) -> None:
            self.name = name
            self.module = None
            self.value = ""

        def load(self) -> object:
            return entry_factory

    class FakeGroups:
        def select(self, group: str):
            assert group == "ccproxy.plugins"
            return [FakeEntryPoint("alpha")]

    monkeypatch.setattr(
        "ccproxy.core.plugins.discovery.entry_points",
        lambda: FakeGroups(),
    )

    plugins_root = tmp_path / "plugins"
    plugins_root.mkdir()

    alpha_dir = plugins_root / "alpha"
    alpha_dir.mkdir()
    (alpha_dir / "plugin.py").write_text(
        """
from ccproxy.core.plugins.declaration import PluginContext, PluginManifest
from ccproxy.core.plugins.interfaces import PluginFactory


class FilesystemFactory(PluginFactory):
    def __init__(self) -> None:
        self._manifest = PluginManifest(name="alpha", version="1.0.0")
        self.source = "filesystem"

    def get_manifest(self) -> PluginManifest:
        return self._manifest

    def create_runtime(self) -> object:
        return object()

    def create_context(self, core_services) -> PluginContext:  # pragma: no cover - not used
        return PluginContext()


factory = FilesystemFactory()
""".strip(),
        encoding="utf-8",
    )

    settings = Settings()
    settings.plugin_discovery.directories = [plugins_root]
    settings.plugins_disable_local_discovery = False

    factories = discover_and_load_plugins(settings)

    assert "alpha" in factories
    assert getattr(factories["alpha"], "source", "") == "filesystem"
