"""Unit tests for the plugin system."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccproxy.models.provider import ProviderConfig
from ccproxy.plugins.registry import PluginRegistry
from ccproxy.services.adapters.base import BaseAdapter


class MockAdapter(BaseAdapter):
    """Mock adapter for testing."""

    async def handle_request(self, request, endpoint, method, **kwargs):
        return MagicMock()

    async def handle_streaming(self, request, endpoint, **kwargs):
        return MagicMock()


class MockPlugin:
    """Mock plugin for testing."""

    @property
    def name(self) -> str:
        return "test_plugin"

    @property
    def version(self) -> str:
        return "1.0.0"

    def create_adapter(self) -> BaseAdapter:
        return MockAdapter()

    def create_config(self) -> ProviderConfig:
        return ProviderConfig(
            name="test_plugin",
            base_url="https://test.example.com",
            supports_streaming=True,
            requires_auth=True,
        )

    async def validate(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_plugin_protocol():
    """Test that MockPlugin implements ProviderPlugin protocol."""
    plugin = MockPlugin()

    # Check protocol attributes
    assert hasattr(plugin, "name")
    assert hasattr(plugin, "version")
    assert hasattr(plugin, "create_adapter")
    assert hasattr(plugin, "create_config")
    assert hasattr(plugin, "validate")

    # Check protocol methods work
    assert plugin.name == "test_plugin"
    assert plugin.version == "1.0.0"
    assert isinstance(plugin.create_adapter(), BaseAdapter)
    assert isinstance(plugin.create_config(), ProviderConfig)
    assert await plugin.validate() is True


@pytest.mark.asyncio
async def test_plugin_registry_register():
    """Test registering a plugin."""
    registry = PluginRegistry()
    plugin = MockPlugin()

    await registry.register(plugin)

    assert "test_plugin" in registry.list_plugins()
    assert registry.get_plugin("test_plugin") is not None
    assert registry.get_adapter("test_plugin") is not None


@pytest.mark.asyncio
async def test_plugin_registry_unregister():
    """Test unregistering a plugin."""
    registry = PluginRegistry()
    plugin = MockPlugin()

    await registry.register(plugin)
    assert "test_plugin" in registry.list_plugins()

    result = await registry.unregister("test_plugin")
    assert result is True
    assert "test_plugin" not in registry.list_plugins()
    assert registry.get_plugin("test_plugin") is None
    assert registry.get_adapter("test_plugin") is None


@pytest.mark.asyncio
async def test_plugin_registry_validation_failure():
    """Test that plugins failing validation are not registered."""
    registry = PluginRegistry()

    # Create a plugin that fails validation
    plugin = MockPlugin()
    plugin.validate = AsyncMock(return_value=False)

    await registry.register(plugin)

    # Plugin should not be registered
    assert "test_plugin" not in registry.list_plugins()
    assert registry.get_plugin("test_plugin") is None
    assert registry.get_adapter("test_plugin") is None


@pytest.mark.asyncio
async def test_plugin_registry_discover_empty_dir():
    """Test discovering plugins in empty directory."""
    registry = PluginRegistry()

    with patch("pathlib.Path.exists", return_value=False):
        plugin_dir = Path("/nonexistent")
        await registry.discover(plugin_dir)

    assert len(registry.list_plugins()) == 0


@pytest.mark.asyncio
async def test_plugin_registry_discover_with_plugin():
    """Test discovering plugins from directory."""
    registry = PluginRegistry()

    # Mock the file system
    mock_path = MagicMock()
    mock_path.exists.return_value = True
    mock_path.glob.return_value = [Path("/plugins/test_plugin.py")]

    # Mock the module loading
    with patch("importlib.util.spec_from_file_location") as mock_spec:
        mock_module = MagicMock()
        mock_module.TestPlugin = MockPlugin

        spec = MagicMock()
        spec.loader = MagicMock()
        mock_spec.return_value = spec

        with patch("importlib.util.module_from_spec", return_value=mock_module):
            # Execute module should set up the TestPlugin class
            def exec_module(module):
                module.TestPlugin = MockPlugin

            spec.loader.exec_module = exec_module

            await registry.load_plugin(Path("/plugins/test_plugin.py"))

    # Check that plugin was discovered and registered
    assert "test_plugin" in registry.list_plugins()


@pytest.mark.asyncio
async def test_plugin_registry_load_invalid_plugin():
    """Test loading an invalid plugin file."""
    registry = PluginRegistry()

    # Mock a plugin that raises an exception
    with patch("importlib.util.spec_from_file_location") as mock_spec:
        spec = MagicMock()
        spec.loader = MagicMock()
        spec.loader.exec_module.side_effect = Exception("Module load error")
        mock_spec.return_value = spec

        with patch("importlib.util.module_from_spec"):
            # Should not raise, just log error
            await registry.load_plugin(Path("/plugins/bad_plugin.py"))

    # No plugins should be registered
    assert len(registry.list_plugins()) == 0


@pytest.mark.asyncio
async def test_base_adapter_interface():
    """Test BaseAdapter interface."""
    adapter = MockAdapter()

    # Test handle_request
    request = MagicMock()
    response = await adapter.handle_request(request, "/test", "GET")
    assert response is not None

    # Test handle_streaming
    stream_response = await adapter.handle_streaming(request, "/test")
    assert stream_response is not None

    # Test optional methods
    validation = await adapter.validate_request(request, "/test")
    assert validation is None  # Default implementation

    data = {"test": "data"}
    transformed = await adapter.transform_request(data)
    assert transformed == data  # Default is no transformation

    response_data = {"response": "data"}
    transformed_response = await adapter.transform_response(response_data)
    assert transformed_response == response_data  # Default is no transformation


@pytest.mark.asyncio
async def test_plugin_registry_reload():
    """Test reloading a plugin."""
    registry = PluginRegistry()
    plugin = MockPlugin()

    # Register initial plugin
    await registry.register(plugin)
    assert "test_plugin" in registry.list_plugins()

    # Mock the reload process
    with patch.object(registry, "load_plugin", new_callable=AsyncMock) as mock_load:
        result = await registry.reload_plugin(
            "test_plugin", Path("/plugins/test_plugin.py")
        )

        # Plugin should be unregistered and reloaded
        mock_load.assert_called_once_with(Path("/plugins/test_plugin.py"))

    # For this test, the plugin won't actually be re-registered
    # because load_plugin is mocked
    assert result is False  # Not found after mock reload
