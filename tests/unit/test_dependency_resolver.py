"""Tests for plugin dependency resolver."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from packaging.version import Version

from ccproxy.plugins.dependency_resolver import (
    DependencyInfo,
    PluginDependencyResolver,
    PluginDependencyResult,
)


@pytest.fixture
def temp_plugin_dir():
    """Create a temporary plugin directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        plugin_dir = Path(tmpdir) / "test_plugin"
        plugin_dir.mkdir()
        yield plugin_dir


@pytest.fixture
def resolver():
    """Create a dependency resolver."""
    return PluginDependencyResolver(auto_install=False, require_user_consent=True)


class TestDependencyInfo:
    """Test DependencyInfo dataclass."""

    def test_dependency_info_creation(self):
        """Test DependencyInfo creation and properties."""
        dep = DependencyInfo(
            name="requests",
            requirement_spec="requests>=2.25.0",
            is_installed=True,
            installed_version="2.28.0",
            meets_requirement=True,
        )

        assert dep.name == "requests"
        assert dep.requirement_spec == "requests>=2.25.0"
        assert dep.is_installed is True
        assert dep.installed_version == "2.28.0"
        assert dep.meets_requirement is True


class TestPluginDependencyResult:
    """Test PluginDependencyResult dataclass."""

    def test_missing_dependencies_property(self):
        """Test missing_dependencies property."""
        result = PluginDependencyResult(
            plugin_name="test",
            plugin_path=Path("/test"),
            dependencies=[
                DependencyInfo("pkg1", "pkg1>=1.0", True, "1.5.0", True),
                DependencyInfo("pkg2", "pkg2>=2.0", False, None, False),
                DependencyInfo("pkg3", "pkg3>=1.0", True, "0.9.0", False),
            ],
        )

        missing = result.missing_dependencies
        assert len(missing) == 2
        assert missing[0].name == "pkg2"
        assert missing[1].name == "pkg3"

    def test_installed_dependencies_property(self):
        """Test installed_dependencies property."""
        result = PluginDependencyResult(
            plugin_name="test",
            plugin_path=Path("/test"),
            dependencies=[
                DependencyInfo("pkg1", "pkg1>=1.0", True, "1.5.0", True),
                DependencyInfo("pkg2", "pkg2>=2.0", False, None, False),
            ],
        )

        installed = result.installed_dependencies
        assert len(installed) == 1
        assert installed[0].name == "pkg1"


class TestPluginDependencyResolver:
    """Test PluginDependencyResolver class."""

    def test_resolver_initialization(self):
        """Test resolver initialization with different parameters."""
        resolver = PluginDependencyResolver(auto_install=True, require_user_consent=False)
        assert resolver.auto_install is True
        assert resolver.require_user_consent is False

    def test_analyze_plugin_no_pyproject(self, resolver, temp_plugin_dir):
        """Test analyzing plugin without pyproject.toml."""
        result = resolver.analyze_plugin_dependencies(temp_plugin_dir)

        assert result.plugin_name == temp_plugin_dir.name
        assert result.plugin_path == temp_plugin_dir
        assert result.has_pyproject is False
        assert result.all_satisfied is True
        assert len(result.dependencies) == 0

    def test_analyze_plugin_empty_dependencies(self, resolver, temp_plugin_dir):
        """Test analyzing plugin with empty dependencies."""
        pyproject_content = """
[project]
name = "test-plugin"
dependencies = []
"""
        (temp_plugin_dir / "pyproject.toml").write_text(pyproject_content)

        result = resolver.analyze_plugin_dependencies(temp_plugin_dir)

        assert result.has_pyproject is True
        assert result.all_satisfied is True
        assert len(result.dependencies) == 0

    @patch("importlib.metadata.version")
    def test_analyze_plugin_with_satisfied_dependencies(
        self, mock_version, resolver, temp_plugin_dir
    ):
        """Test analyzing plugin with satisfied dependencies."""
        mock_version.return_value = "2.28.0"

        pyproject_content = """
[project]
name = "test-plugin"
dependencies = ["requests>=2.25.0"]
"""
        (temp_plugin_dir / "pyproject.toml").write_text(pyproject_content)

        result = resolver.analyze_plugin_dependencies(temp_plugin_dir)

        assert result.has_pyproject is True
        assert result.all_satisfied is True
        assert len(result.dependencies) == 1
        assert result.dependencies[0].name == "requests"
        assert result.dependencies[0].meets_requirement is True

    @patch("importlib.metadata.version")
    def test_analyze_plugin_with_missing_dependencies(
        self, mock_version, resolver, temp_plugin_dir
    ):
        """Test analyzing plugin with missing dependencies."""
        from importlib.metadata import PackageNotFoundError

        mock_version.side_effect = PackageNotFoundError("requests")

        pyproject_content = """
[project]
name = "test-plugin"
dependencies = ["requests>=2.25.0"]
"""
        (temp_plugin_dir / "pyproject.toml").write_text(pyproject_content)

        result = resolver.analyze_plugin_dependencies(temp_plugin_dir)

        assert result.has_pyproject is True
        assert result.all_satisfied is False
        assert len(result.dependencies) == 1
        assert result.dependencies[0].name == "requests"
        assert result.dependencies[0].is_installed is False
        assert result.dependencies[0].meets_requirement is False

    @patch("importlib.metadata.version")
    def test_analyze_plugin_with_version_mismatch(
        self, mock_version, resolver, temp_plugin_dir
    ):
        """Test analyzing plugin with version that doesn't meet requirements."""
        mock_version.return_value = "2.20.0"

        pyproject_content = """
[project]
name = "test-plugin"
dependencies = ["requests>=2.25.0"]
"""
        (temp_plugin_dir / "pyproject.toml").write_text(pyproject_content)

        result = resolver.analyze_plugin_dependencies(temp_plugin_dir)

        assert result.has_pyproject is True
        assert result.all_satisfied is False
        assert len(result.dependencies) == 1
        assert result.dependencies[0].name == "requests"
        assert result.dependencies[0].is_installed is True
        assert result.dependencies[0].meets_requirement is False

    def test_analyze_dependency_valid_spec(self, resolver):
        """Test analyzing a valid dependency specification."""
        dep_info = resolver._analyze_dependency("requests>=2.25.0")

        assert dep_info.name == "requests"
        assert dep_info.requirement_spec == "requests>=2.25.0"

    def test_analyze_dependency_invalid_spec(self, resolver):
        """Test analyzing an invalid dependency specification."""
        dep_info = resolver._analyze_dependency("invalid-spec!")

        assert dep_info.name == "invalid-spec!"  # Fallback name extraction
        assert dep_info.error is not None

    @pytest.mark.asyncio
    async def test_resolve_dependencies_already_satisfied(self, resolver):
        """Test resolving dependencies when already satisfied."""
        result = PluginDependencyResult(
            plugin_name="test",
            plugin_path=Path("/test"),
            all_satisfied=True,
        )

        success = await resolver.resolve_dependencies(result)
        assert success is True

    @pytest.mark.asyncio
    async def test_resolve_dependencies_auto_install_disabled(self, resolver):
        """Test resolving dependencies with auto-install disabled."""
        result = PluginDependencyResult(
            plugin_name="test",
            plugin_path=Path("/test"),
            all_satisfied=False,
            dependencies=[
                DependencyInfo("missing-pkg", "missing-pkg>=1.0", False, None, False)
            ],
        )

        success = await resolver.resolve_dependencies(result)
        assert success is False

    @pytest.mark.asyncio
    async def test_resolve_dependencies_user_consent_declined(self):
        """Test resolving dependencies when user declines consent."""
        resolver = PluginDependencyResolver(auto_install=True, require_user_consent=True)

        result = PluginDependencyResult(
            plugin_name="test",
            plugin_path=Path("/test"),
            all_satisfied=False,
            dependencies=[
                DependencyInfo("missing-pkg", "missing-pkg>=1.0", False, None, False)
            ],
        )

        async def mock_consent_callback(plugin_name, missing_deps):
            return False

        success = await resolver.resolve_dependencies(result, mock_consent_callback)
        assert success is False

    @patch("subprocess.run")
    @pytest.mark.asyncio
    async def test_install_plugin_dependencies_success(self, mock_run, resolver):
        """Test successful dependency installation."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="Success", stderr=""
        )

        plugin_dir = Path("/test/plugin")
        missing_deps = [
            DependencyInfo("missing-pkg", "missing-pkg>=1.0", False, None, False)
        ]

        success = await resolver._install_plugin_dependencies(plugin_dir, missing_deps)
        assert success is True

        mock_run.assert_called_once_with(
            ["uv", "pip", "install", "-e", str(plugin_dir)],
            capture_output=True,
            text=True,
            check=True,
            timeout=300,
        )

    @patch("subprocess.run")
    @pytest.mark.asyncio
    async def test_install_plugin_dependencies_failure(self, mock_run, resolver):
        """Test failed dependency installation."""
        from subprocess import CalledProcessError

        error = CalledProcessError(1, ["uv"])
        error.stderr = "Installation failed"
        error.stdout = ""
        mock_run.side_effect = error

        plugin_dir = Path("/test/plugin")
        missing_deps = [
            DependencyInfo("missing-pkg", "missing-pkg>=1.0", False, None, False)
        ]

        success = await resolver._install_plugin_dependencies(plugin_dir, missing_deps)
        assert success is False

    @patch("subprocess.run")
    def test_install_specific_dependencies_success(self, mock_run, resolver):
        """Test successful installation of specific dependencies."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="Success", stderr=""
        )

        dependencies = ["requests>=2.25.0", "httpx>=0.24.0"]
        success = resolver.install_specific_dependencies(dependencies)
        assert success is True

        mock_run.assert_called_once_with(
            ["uv", "pip", "install", "requests>=2.25.0", "httpx>=0.24.0"],
            capture_output=True,
            text=True,
            check=True,
            timeout=300,
        )

    @patch("subprocess.run")
    def test_check_system_requirements(self, mock_run, resolver):
        """Test system requirements checking."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="uv 0.4.15", stderr=""
        )

        checks = resolver.check_system_requirements()

        assert "python_version" in checks
        assert "uv_available" in checks
        assert "uv_version" in checks
        assert checks["uv_available"] is True
        assert checks["python_version"]["meets_minimum"] is True

    def test_generate_dependency_report(self, resolver):
        """Test dependency report generation."""
        results = [
            PluginDependencyResult(
                plugin_name="plugin1",
                plugin_path=Path("/test/plugin1"),
                has_pyproject=True,
                all_satisfied=True,
                dependencies=[
                    DependencyInfo("pkg1", "pkg1>=1.0", True, "1.5.0", True)
                ],
            ),
            PluginDependencyResult(
                plugin_name="plugin2",
                plugin_path=Path("/test/plugin2"),
                has_pyproject=True,
                all_satisfied=False,
                dependencies=[
                    DependencyInfo("pkg2", "pkg2>=2.0", False, None, False)
                ],
            ),
        ]

        report = resolver.generate_dependency_report(results)

        assert report["total_plugins"] == 2
        assert report["plugins_with_dependencies"] == 2
        assert report["plugins_satisfied"] == 1
        assert report["plugins_with_issues"] == 1
        assert len(report["plugin_details"]) == 2
        assert "system_checks" in report