"""Unit tests for the plugin system."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import APIRouter

from ccproxy.models.provider import ProviderConfig
from ccproxy.plugins.protocol import HealthCheckResult
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

    @property
    def router_prefix(self) -> str:
        return "/test"

    async def initialize(self, services) -> None:
        """Initialize plugin with shared services."""
        pass

    async def shutdown(self) -> None:
        """Perform graceful shutdown."""
        pass

    def create_adapter(self) -> BaseAdapter:
        return MockAdapter()

    def create_config(self) -> ProviderConfig:
        return ProviderConfig(
            name="test_plugin",
            base_url="https://test.example.com",
            supports_streaming=True,
            requires_auth=True,
        )

    def get_config_class(self):
        """Return configuration class for the plugin."""
        return None

    async def validate(self) -> bool:
        return True

    def get_routes(self) -> APIRouter | None:
        """Get plugin-specific routes (optional)."""
        return None

    async def health_check(self) -> HealthCheckResult:
        """Perform health check following IETF format."""
        return HealthCheckResult(
            status="pass",
            componentId="test_plugin",
            componentType="provider_plugin",
            output="Plugin is healthy",
            version=self.version,
        )

    def get_scheduled_tasks(self):
        """Get scheduled task definitions for this plugin (optional)."""
        return None


@pytest.mark.asyncio
async def test_plugin_protocol():
    """Test that MockPlugin implements ProviderPlugin protocol."""
    plugin = MockPlugin()

    # Check protocol attributes
    assert hasattr(plugin, "name")
    assert hasattr(plugin, "version")
    assert hasattr(plugin, "router_prefix")
    assert hasattr(plugin, "initialize")
    assert hasattr(plugin, "shutdown")
    assert hasattr(plugin, "create_adapter")
    assert hasattr(plugin, "create_config")
    assert hasattr(plugin, "validate")
    assert hasattr(plugin, "get_routes")
    assert hasattr(plugin, "health_check")
    assert hasattr(plugin, "get_scheduled_tasks")

    # Check protocol methods work
    assert plugin.name == "test_plugin"
    assert plugin.version == "1.0.0"
    assert plugin.router_prefix == "/test"
    assert isinstance(plugin.create_adapter(), BaseAdapter)
    assert isinstance(plugin.create_config(), ProviderConfig)
    assert await plugin.validate() is True
    assert plugin.get_routes() is None
    health_result = await plugin.health_check()
    assert isinstance(health_result, HealthCheckResult)
    assert health_result.status == "pass"
    assert plugin.get_scheduled_tasks() is None


@pytest.mark.asyncio
async def test_plugin_registry_register():
    """Test registering a plugin."""
    registry = PluginRegistry()
    plugin = MockPlugin()

    await registry.register_and_initialize(plugin)

    assert "test_plugin" in registry.list_plugins()
    assert registry.get_plugin("test_plugin") is not None
    assert registry.get_adapter("test_plugin") is not None


@pytest.mark.asyncio
async def test_plugin_registry_unregister():
    """Test unregistering a plugin."""
    registry = PluginRegistry()
    plugin = MockPlugin()

    await registry.register_and_initialize(plugin)
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
    # Mock the validate method to return False
    with patch.object(plugin, "validate", AsyncMock(return_value=False)):
        await registry.register_and_initialize(plugin)

    # Plugin should not be registered
    assert "test_plugin" not in registry.list_plugins()
    assert registry.get_plugin("test_plugin") is None
    assert registry.get_adapter("test_plugin") is None


@pytest.mark.asyncio
async def test_plugin_registry_discover_empty_dir():
    """Test discovering plugins with no plugins available."""
    registry = PluginRegistry()

    # Mock CoreServices
    mock_services = MagicMock()

    # Mock PluginLoader to return no plugins
    with patch("ccproxy.plugins.registry.PluginLoader") as mock_loader_class:
        mock_loader = MagicMock()
        # Changed to load_plugins_with_paths to match new implementation
        mock_loader.load_plugins_with_paths = MagicMock(return_value=[])
        mock_loader_class.return_value = mock_loader

        await registry.discover_and_initialize(mock_services)

    assert len(registry.list_plugins()) == 0


@pytest.mark.asyncio
async def test_plugin_registry_discover_with_plugin():
    """Test discovering plugins with plugins available."""
    registry = PluginRegistry()

    # Mock CoreServices
    mock_services = MagicMock()

    # Mock PluginLoader to return a plugin
    with patch("ccproxy.plugins.registry.PluginLoader") as mock_loader_class:
        mock_loader = MagicMock()
        mock_plugin = MockPlugin()
        # Changed to load_plugins_with_paths to match new implementation
        mock_loader.load_plugins_with_paths = MagicMock(
            return_value=[(mock_plugin, None)]
        )
        mock_loader_class.return_value = mock_loader

        await registry.discover_and_initialize(mock_services)

    # Check that plugin was discovered and registered
    assert "test_plugin" in registry.list_plugins()


@pytest.mark.asyncio
async def test_plugin_registry_load_invalid_plugin():
    """Test handling invalid plugins during discovery."""
    registry = PluginRegistry()

    # Mock CoreServices
    mock_services = MagicMock()

    # Create a plugin that will fail during registration
    class BadPlugin(MockPlugin):
        async def validate(self):
            raise Exception("Plugin validation error")

    # Mock PluginLoader to return a bad plugin
    with patch("ccproxy.plugins.registry.PluginLoader") as mock_loader_class:
        mock_loader = MagicMock()
        bad_plugin = BadPlugin()
        # Changed to load_plugins_with_paths to match new implementation
        mock_loader.load_plugins_with_paths = MagicMock(
            return_value=[(bad_plugin, None)]
        )
        mock_loader_class.return_value = mock_loader

        # Should not raise, just log error
        await registry.discover_and_initialize(mock_services)

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
    await registry.register_and_initialize(plugin)
    assert "test_plugin" in registry.list_plugins()

    # Store a mock path for the plugin
    registry._plugin_paths["test_plugin"] = Path("/plugins/test_plugin/plugin.py")

    # Mock the reload process - the reload_plugin method uses PluginLoader internally
    with patch("ccproxy.plugins.registry.PluginLoader") as mock_loader_class:
        mock_loader = MagicMock()
        # Mock that no plugins are found during reload (simulating a failed reload)
        mock_loader.load_single_plugin.return_value = None
        mock_loader_class.return_value = mock_loader

        result = await registry.reload_plugin("test_plugin")

    # For this test, the plugin won't actually be re-registered
    # because load_single_plugin returns None
    assert result is False  # Not found after mock reload
