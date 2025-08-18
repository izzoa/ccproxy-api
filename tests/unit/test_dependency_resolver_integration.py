"""Integration tests for dependency resolver with plugin system."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from ccproxy.plugins.loader import PluginLoader
from ccproxy.plugins.registry import PluginRegistry


@pytest.fixture
def temp_plugin_with_deps():
    """Create a temporary plugin with dependencies."""
    with tempfile.TemporaryDirectory() as tmpdir:
        plugin_dir = Path(tmpdir) / "test_plugin"
        plugin_dir.mkdir()

        # Create pyproject.toml with dependencies
        pyproject_content = """
[project]
name = "test-plugin"
dependencies = ["requests>=2.25.0"]
"""
        (plugin_dir / "pyproject.toml").write_text(pyproject_content)

        # Create plugin.py
        plugin_content = """
from ccproxy.plugins.protocol import ProviderPlugin
from ccproxy.services.adapters.base import BaseAdapter

class TestAdapter(BaseAdapter):
    async def handle_request(self, request, endpoint, method):
        return {"test": "response"}

class Plugin(ProviderPlugin):
    @property
    def name(self) -> str:
        return "test_plugin"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def router_prefix(self) -> str:
        return "/test"

    async def initialize(self, services):
        pass

    async def shutdown(self):
        pass

    def create_adapter(self):
        return TestAdapter()

    def create_config(self):
        from ccproxy.models.provider import ProviderConfig
        return ProviderConfig(name="test")

    async def validate(self) -> bool:
        return True

    def get_routes(self):
        return None

    async def health_check(self):
        from ccproxy.plugins.protocol import HealthCheckResult
        return HealthCheckResult(
            status="pass",
            componentId="test-plugin",
            output="Plugin is healthy"
        )
"""
        (plugin_dir / "plugin.py").write_text(plugin_content)

        yield plugin_dir


class TestDependencyResolverIntegration:
    """Test dependency resolver integration with plugin system."""

    @patch("importlib.metadata.version")
    def test_loader_with_satisfied_dependencies(
        self, mock_version, temp_plugin_with_deps
    ):
        """Test loader successfully loads plugin with satisfied dependencies."""
        mock_version.return_value = "2.28.0"

        loader = PluginLoader(auto_install=False)

        # Test dependency checking
        success = loader._check_plugin_dependencies(temp_plugin_with_deps)
        assert success is True

    @patch("importlib.metadata.version")
    def test_loader_with_missing_dependencies(
        self, mock_version, temp_plugin_with_deps
    ):
        """Test loader skips plugin with missing dependencies."""
        from importlib.metadata import PackageNotFoundError

        mock_version.side_effect = PackageNotFoundError("requests")

        loader = PluginLoader(auto_install=False)

        # Test dependency checking
        success = loader._check_plugin_dependencies(temp_plugin_with_deps)
        assert success is False

    @patch("importlib.metadata.version")
    def test_loader_dependency_report(self, mock_version, temp_plugin_with_deps):
        """Test loader generates comprehensive dependency report."""
        mock_version.return_value = "2.28.0"

        loader = PluginLoader(auto_install=False)

        report = loader.get_dependency_report([temp_plugin_with_deps])

        assert report["total_plugins"] == 1
        assert report["plugins_with_dependencies"] == 1
        assert report["plugins_satisfied"] == 1
        assert report["plugins_with_issues"] == 0

        plugin_detail = report["plugin_details"][0]
        assert plugin_detail["name"] == "test_plugin"
        assert plugin_detail["all_satisfied"] is True
        assert plugin_detail["total_dependencies"] == 1

        dep = plugin_detail["dependencies"][0]
        assert dep["name"] == "requests"
        assert dep["satisfied"] is True

    @patch("importlib.metadata.version")
    @pytest.mark.asyncio
    async def test_registry_with_dependency_management(
        self, mock_version, temp_plugin_with_deps
    ):
        """Test registry can handle plugins with dependency management."""
        mock_version.return_value = "2.28.0"

        registry = PluginRegistry(auto_install_deps=False, require_user_consent=True)

        # Mock services
        services = AsyncMock()
        registry._services = services

        # Load plugin with dependency checking
        loader = PluginLoader(auto_install=False)

        # This would normally fail if dependencies weren't satisfied
        success = loader._check_plugin_dependencies(temp_plugin_with_deps)
        assert success is True

        # Load the plugin
        plugin = loader.load_single_plugin(temp_plugin_with_deps)
        assert plugin is not None
        assert plugin.name == "test_plugin"

    @patch("importlib.metadata.version")
    @pytest.mark.asyncio
    async def test_registry_dependency_report(
        self, mock_version, temp_plugin_with_deps
    ):
        """Test registry can generate dependency reports."""
        mock_version.return_value = "2.28.0"

        registry = PluginRegistry(auto_install_deps=False)

        # Store plugin path manually for testing
        registry._plugin_paths["test_plugin"] = temp_plugin_with_deps / "plugin.py"

        report = await registry.get_dependency_report()

        assert "total_plugins" in report
        assert "system_checks" in report
        assert report["system_checks"]["python_version"]["meets_minimum"] is True

    @patch("subprocess.run")
    @patch("importlib.metadata.version")
    @pytest.mark.asyncio
    async def test_registry_resolve_dependencies(
        self, mock_version, mock_run, temp_plugin_with_deps
    ):
        """Test registry can resolve dependencies for plugins."""
        from importlib.metadata import PackageNotFoundError

        mock_version.side_effect = PackageNotFoundError("requests")
        mock_run.return_value = AsyncMock(returncode=0, stdout="Success", stderr="")

        registry = PluginRegistry(auto_install_deps=True, require_user_consent=False)

        # Store plugin path manually for testing
        registry._plugin_paths["test_plugin"] = temp_plugin_with_deps / "plugin.py"

        results = await registry.resolve_all_dependencies()

        assert "test_plugin" in results
        # Note: In a real test environment this might fail due to subprocess,
        # but the structure should be correct
