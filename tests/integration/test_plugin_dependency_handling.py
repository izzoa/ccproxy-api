"""Integration tests covering plugin dependency handling during discovery."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ccproxy.api.app import create_app, initialize_plugins_startup
from ccproxy.api.bootstrap import create_service_container
from ccproxy.config.core import LoggingSettings
from ccproxy.config.settings import Settings
from ccproxy.core.logging import setup_logging


pytestmark = [pytest.mark.integration, pytest.mark.codex]


PLUGIN_DIR = Path(__file__).parents[2] / "ccproxy" / "plugins"


def _available_plugins() -> set[str]:
    """Return filesystem plugin package names."""

    if not PLUGIN_DIR.exists():
        return set()
    return {
        entry.name
        for entry in PLUGIN_DIR.iterdir()
        if entry.is_dir() and (entry / "plugin.py").exists()
    }


def _make_settings(
    *,
    plugin_configs: dict[str, dict[str, Any]],
    enabled_plugins: list[str] | None = None,
    disabled_plugins: list[str] | None = None,
) -> Settings:
    """Construct Settings limiting active plugins to the supplied configs."""

    available = _available_plugins()
    explicit_disabled = set(disabled_plugins or [])
    requested = set(plugin_configs.keys())

    if enabled_plugins is not None:
        allowed = set(enabled_plugins)
        explicit_disabled.update(name for name in available if name not in allowed)
    else:
        explicit_disabled.update(name for name in available if name not in requested)

    normalized_configs = dict(plugin_configs)
    if "duckdb_storage" not in normalized_configs:
        normalized_configs["duckdb_storage"] = {"enabled": False}
        explicit_disabled.add("duckdb_storage")

    final_disabled: list[str] | None
    if disabled_plugins is not None:
        final_disabled = disabled_plugins
    else:
        final_disabled = sorted(explicit_disabled) if explicit_disabled else None

    return Settings(
        enable_plugins=True,
        plugins_disable_local_discovery=False,
        enabled_plugins=enabled_plugins,
        disabled_plugins=final_disabled,
        plugins=normalized_configs,
        logging=LoggingSettings(
            **{
                "level": "DEBUG",
                "verbose_api": False,
            }
        ),
    )


async def _get_plugin_status(settings: Settings) -> dict[str, Any]:
    """Initialize an app with the given settings and return /plugins/status payload."""

    setup_logging(json_logs=False, log_level_name="DEBUG")
    service_container = create_service_container(settings)
    app = create_app(service_container)
    await initialize_plugins_startup(app, settings)

    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/plugins/status")
        assert response.status_code == 200
        return response.json()


def _find_plugin_entry(payload: dict[str, Any], name: str) -> dict[str, Any] | None:
    for entry in payload.get("plugins", []):
        if entry.get("name") == name:
            return entry
    return None


@pytest.mark.asyncio(loop_scope="module")
async def test_codex_skipped_when_dependency_disabled_by_config() -> None:
    """Codex registers but does not initialize when oauth_codex config disables it."""

    settings = _make_settings(
        plugin_configs={
            "codex": {"enabled": True},
            "oauth_codex": {"enabled": False},
        }
    )

    payload = await _get_plugin_status(settings)
    codex_entry = _find_plugin_entry(payload, "codex")

    assert codex_entry is not None
    assert codex_entry["initialized"] is False
    assert "oauth_codex" not in {entry["name"] for entry in payload.get("plugins", [])}
    assert "codex" not in payload.get("initialization_order", [])


@pytest.mark.asyncio(loop_scope="module")
async def test_codex_skipped_when_dependency_not_whitelisted() -> None:
    """enabled_plugins whitelist excludes oauth_codex so codex dependency is missing."""

    settings = _make_settings(
        plugin_configs={"codex": {"enabled": True}},
        enabled_plugins=["codex"],
    )

    payload = await _get_plugin_status(settings)
    codex_entry = _find_plugin_entry(payload, "codex")

    assert codex_entry is not None
    assert codex_entry["initialized"] is False
    assert {entry["name"] for entry in payload.get("plugins", [])} == {"codex"}
    assert "codex" not in payload.get("initialization_order", [])
    assert payload.get("services", {}) == {}


@pytest.mark.asyncio(loop_scope="module")
async def test_codex_initializes_with_dependency_enabled() -> None:
    """When both codex and oauth_codex participate, codex should initialize."""

    settings = _make_settings(
        plugin_configs={
            "codex": {"enabled": True},
            "oauth_codex": {"enabled": True},
        },
        enabled_plugins=["codex", "oauth_codex"],
    )

    payload = await _get_plugin_status(settings)

    codex_entry = _find_plugin_entry(payload, "codex")
    oauth_entry = _find_plugin_entry(payload, "oauth_codex")

    assert codex_entry is not None and codex_entry["initialized"] is True
    assert oauth_entry is not None and oauth_entry["initialized"] is True
    assert "codex" in payload.get("initialization_order", [])
    assert "oauth_codex" in payload.get("initialization_order", [])


@pytest.mark.asyncio(loop_scope="module")
async def test_codex_removed_when_plugin_config_disables_it() -> None:
    """plugins.codex.enabled=False removes codex even if dependency is active."""

    settings = _make_settings(
        plugin_configs={
            "codex": {"enabled": False},
            "oauth_codex": {"enabled": True},
        }
    )

    payload = await _get_plugin_status(settings)

    assert _find_plugin_entry(payload, "codex") is None
    oauth_entry = _find_plugin_entry(payload, "oauth_codex")
    assert oauth_entry is not None and oauth_entry["initialized"] is True


@pytest.mark.asyncio(loop_scope="module")
async def test_enabled_plugins_whitelist_is_respected() -> None:
    """Only explicitly whitelisted plugins load when enabled_plugins is set."""

    settings = _make_settings(
        plugin_configs={},
        enabled_plugins=["oauth_codex"],
    )

    payload = await _get_plugin_status(settings)

    assert {entry["name"] for entry in payload.get("plugins", [])} == {"oauth_codex"}
    oauth_entry = _find_plugin_entry(payload, "oauth_codex")
    assert oauth_entry is not None


@pytest.mark.asyncio(loop_scope="module")
async def test_disabled_plugins_blacklist_is_respected() -> None:
    """All plugins except the disabled ones remain available when blacklist is set."""

    available = _available_plugins()
    target_disable = {"codex", "oauth_codex"}

    settings = _make_settings(
        plugin_configs={},
        disabled_plugins=list(target_disable),
    )

    payload = await _get_plugin_status(settings)

    loaded = {entry["name"] for entry in payload.get("plugins", [])}

    assert target_disable.isdisjoint(loaded)
    expected = available - target_disable - {"duckdb_storage"}
    assert loaded == expected
