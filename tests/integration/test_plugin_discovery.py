import pytest

from ccproxy.api.app import create_app, initialize_plugins_startup
from ccproxy.api.bootstrap import create_service_container
from ccproxy.config.core import LoggingSettings
from ccproxy.config.settings import Settings
from ccproxy.core.plugins.factories import PluginRegistry


@pytest.mark.unit
async def test_load_all_skips_enabled():
    enabled_plugins = ["codex", "oauth_codex"]
    plugin_configs = {"codex": {"enabled": False}, "oauth_codex": {"enabled": False}}
    settings = Settings(
        enable_plugins=True,
        plugins_disable_local_discovery=True,
        enabled_plugins=enabled_plugins,
        plugins=plugin_configs,
        logging=LoggingSettings(
            **{
                "level": "TRACE",
                "verbose_api": False,
            }
        ),
    )

    # setup_logging(json_logs=False, log_level_name="DEBUG")

    service_container = create_service_container(settings)
    app = create_app(service_container)

    print(app.state.plugin_registry)
    assert app.state.plugin_registry is not None
    print(app.state.plugin_registry.list_plugins())
    await initialize_plugins_startup(app, settings)
    print(app.state.plugin_registry.list_plugins())
    plugin_registry: PluginRegistry = app.state.plugin_registry
    assert set(plugin_registry.list_plugins()) == set(enabled_plugins)
