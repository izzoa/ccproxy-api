"""Plugin dependency resolution and management."""

import importlib.metadata
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
from packaging.requirements import Requirement
from packaging.version import Version


logger = structlog.get_logger(__name__)


@dataclass
class DependencyInfo:
    """Information about a single dependency."""

    name: str
    requirement_spec: str
    is_installed: bool = False
    installed_version: str | None = None
    meets_requirement: bool = False
    error: str | None = None


@dataclass
class PluginDependencyResult:
    """Result of plugin dependency analysis."""

    plugin_name: str
    plugin_path: Path
    dependencies: list[DependencyInfo] = field(default_factory=list)
    all_satisfied: bool = False
    has_pyproject: bool = False
    error: str | None = None

    @property
    def missing_dependencies(self) -> list[DependencyInfo]:
        """Get list of dependencies that are missing or don't meet requirements."""
        return [dep for dep in self.dependencies if not dep.meets_requirement]

    @property
    def installed_dependencies(self) -> list[DependencyInfo]:
        """Get list of dependencies that are properly installed."""
        return [dep for dep in self.dependencies if dep.meets_requirement]


class PluginDependencyResolver:
    """Handles plugin dependency resolution and management."""

    def __init__(self, auto_install: bool = False, require_user_consent: bool = True):
        """Initialize dependency resolver.

        Args:
            auto_install: Whether to automatically install missing dependencies
            require_user_consent: Whether to require user consent before installing
        """
        self.auto_install = auto_install
        self.require_user_consent = require_user_consent

    def analyze_plugin_dependencies(self, plugin_dir: Path) -> PluginDependencyResult:
        """Analyze dependencies for a plugin.

        Args:
            plugin_dir: Path to the plugin directory

        Returns:
            PluginDependencyResult with analysis results
        """
        result = PluginDependencyResult(
            plugin_name=plugin_dir.name, plugin_path=plugin_dir
        )

        pyproject_path = plugin_dir / "pyproject.toml"
        if not pyproject_path.exists():
            # No pyproject.toml, assume plugin has no extra dependencies
            result.all_satisfied = True
            result.has_pyproject = False
            logger.debug(f"No pyproject.toml found for plugin {plugin_dir.name}")
            return result

        result.has_pyproject = True

        try:
            # Parse pyproject.toml to extract dependencies
            import tomllib

            with pyproject_path.open("rb") as f:
                data = tomllib.load(f)

            dependencies = data.get("project", {}).get("dependencies", [])
            if not dependencies:
                result.all_satisfied = True
                logger.debug(f"No dependencies specified for plugin {plugin_dir.name}")
                return result

            # Analyze each dependency
            for dep_spec in dependencies:
                dep_info = self._analyze_dependency(dep_spec)
                result.dependencies.append(dep_info)

            # Check if all dependencies are satisfied
            result.all_satisfied = all(
                dep.meets_requirement for dep in result.dependencies
            )

            if not result.all_satisfied:
                missing = [dep.name for dep in result.missing_dependencies]
                logger.warning(
                    f"Plugin {plugin_dir.name} has unsatisfied dependencies",
                    missing_dependencies=missing,
                )

        except Exception as e:
            error_msg = f"Failed to analyze dependencies: {e}"
            result.error = error_msg
            logger.error(
                f"Error analyzing dependencies for {plugin_dir.name}: {error_msg}",
                exc_info=True,
            )

        return result

    def _analyze_dependency(self, dep_spec: str) -> DependencyInfo:
        """Analyze a single dependency specification.

        Args:
            dep_spec: Dependency specification (e.g., "requests>=2.25.0")

        Returns:
            DependencyInfo with analysis results
        """
        dep_info = DependencyInfo(name="", requirement_spec=dep_spec)

        try:
            # Parse the requirement specification
            req = Requirement(dep_spec)
            dep_info.name = req.name

            # Check if package is installed
            try:
                installed_version = importlib.metadata.version(req.name)
                dep_info.is_installed = True
                dep_info.installed_version = installed_version

                # Check if installed version meets requirement
                if req.specifier:
                    installed_ver = Version(installed_version)
                    dep_info.meets_requirement = installed_ver in req.specifier
                else:
                    # No version constraint, any installed version is fine
                    dep_info.meets_requirement = True

                if not dep_info.meets_requirement:
                    dep_info.error = f"Version {installed_version} does not meet requirement {req.specifier}"

            except importlib.metadata.PackageNotFoundError:
                dep_info.is_installed = False
                dep_info.meets_requirement = False
                dep_info.error = "Package not installed"

        except Exception as e:
            dep_info.error = f"Failed to parse requirement: {e}"
            # Try to extract package name as fallback
            dep_info.name = (
                dep_spec.split(">=")[0]
                .split("==")[0]
                .split("<")[0]
                .split(">")[0]
                .strip()
            )

        return dep_info

    async def resolve_dependencies(
        self, result: PluginDependencyResult, user_consent_callback: Any = None
    ) -> bool:
        """Resolve missing dependencies for a plugin.

        Args:
            result: Plugin dependency analysis result
            user_consent_callback: Optional callback to get user consent

        Returns:
            True if all dependencies are resolved, False otherwise
        """
        if result.all_satisfied:
            return True

        if not self.auto_install:
            logger.info(
                f"Auto-install disabled. Plugin {result.plugin_name} has missing dependencies.",
                missing=[dep.name for dep in result.missing_dependencies],
                suggestion="Run 'uv sync' to install all workspace dependencies",
            )
            return False

        missing_deps = result.missing_dependencies
        if not missing_deps:
            return True

        # Get user consent if required
        if self.require_user_consent and user_consent_callback:
            consent = await user_consent_callback(result.plugin_name, missing_deps)
            if not consent:
                logger.info(
                    f"User declined to install dependencies for {result.plugin_name}"
                )
                return False

        # Attempt to install missing dependencies
        return await self._install_plugin_dependencies(result.plugin_path, missing_deps)

    async def _install_plugin_dependencies(
        self, plugin_dir: Path, missing_deps: list[DependencyInfo]
    ) -> bool:
        """Install missing plugin dependencies.

        Args:
            plugin_dir: Path to the plugin directory
            missing_deps: List of missing dependency info

        Returns:
            True if installation succeeded
        """
        try:
            logger.info(
                f"Installing dependencies for plugin {plugin_dir.name}",
                dependencies=[dep.requirement_spec for dep in missing_deps],
            )

            # Use uv to install the plugin in editable mode
            # This will install all dependencies from pyproject.toml
            result = subprocess.run(
                ["uv", "pip", "install", "-e", str(plugin_dir)],
                capture_output=True,
                text=True,
                check=True,
                timeout=300,  # 5 minute timeout
            )

            logger.info(
                f"Successfully installed dependencies for {plugin_dir.name}",
                stdout=result.stdout.strip() if result.stdout else None,
            )
            return True

        except subprocess.CalledProcessError as e:
            logger.error(
                f"Failed to install dependencies for {plugin_dir.name}",
                stderr=e.stderr.strip() if e.stderr else None,
                stdout=e.stdout.strip() if e.stdout else None,
                returncode=e.returncode,
            )
            return False

        except subprocess.TimeoutExpired:
            logger.error(f"Timeout installing dependencies for {plugin_dir.name}")
            return False

        except FileNotFoundError:
            logger.error(
                "uv command not found. Please install uv package manager.",
                install_docs="https://docs.astral.sh/uv/getting-started/installation/",
            )
            return False

    def install_specific_dependencies(self, dependencies: list[str]) -> bool:
        """Install specific dependencies using uv.

        Args:
            dependencies: List of dependency specifications

        Returns:
            True if installation succeeded
        """
        if not dependencies:
            return True

        try:
            logger.info("Installing specific dependencies", dependencies=dependencies)

            cmd = ["uv", "pip", "install"] + dependencies
            result = subprocess.run(
                cmd, capture_output=True, text=True, check=True, timeout=300
            )

            logger.info(
                "Successfully installed specific dependencies",
                dependencies=dependencies,
                stdout=result.stdout.strip() if result.stdout else None,
            )
            return True

        except subprocess.CalledProcessError as e:
            logger.error(
                "Failed to install specific dependencies",
                dependencies=dependencies,
                stderr=e.stderr.strip() if e.stderr else None,
                returncode=e.returncode,
            )
            return False

        except subprocess.TimeoutExpired:
            logger.error(
                "Timeout installing specific dependencies", dependencies=dependencies
            )
            return False

        except FileNotFoundError:
            logger.error(
                "uv command not found. Please install uv package manager.",
                install_docs="https://docs.astral.sh/uv/getting-started/installation/",
            )
            return False

    def check_system_requirements(self) -> dict[str, Any]:
        """Check system requirements for dependency resolution.

        Returns:
            Dictionary with system check results
        """
        checks: dict[str, Any] = {
            "python_version": None,
            "uv_available": False,
            "uv_version": None,
        }

        # Check Python version
        checks["python_version"] = {
            "version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "meets_minimum": sys.version_info >= (3, 11),
        }

        # Check if uv is available
        try:
            result = subprocess.run(
                ["uv", "--version"],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            checks["uv_available"] = True
            checks["uv_version"] = result.stdout.strip()
        except (
            subprocess.CalledProcessError,
            FileNotFoundError,
            subprocess.TimeoutExpired,
        ):
            checks["uv_available"] = False

        return checks

    def generate_dependency_report(
        self, results: list[PluginDependencyResult]
    ) -> dict[str, Any]:
        """Generate a comprehensive dependency report.

        Args:
            results: List of plugin dependency analysis results

        Returns:
            Dictionary with dependency report
        """
        report: dict[str, Any] = {
            "total_plugins": len(results),
            "plugins_with_dependencies": 0,
            "plugins_satisfied": 0,
            "plugins_with_issues": 0,
            "system_checks": self.check_system_requirements(),
            "plugin_details": [],
        }

        for result in results:
            if result.has_pyproject and result.dependencies:
                report["plugins_with_dependencies"] += 1

            if result.all_satisfied:
                report["plugins_satisfied"] += 1
            else:
                report["plugins_with_issues"] += 1

            plugin_detail: dict[str, Any] = {
                "name": result.plugin_name,
                "path": str(result.plugin_path),
                "has_pyproject": result.has_pyproject,
                "all_satisfied": result.all_satisfied,
                "total_dependencies": len(result.dependencies),
                "missing_count": len(result.missing_dependencies),
                "error": result.error,
            }

            if result.dependencies:
                plugin_detail["dependencies"] = [
                    {
                        "name": dep.name,
                        "requirement": dep.requirement_spec,
                        "installed": dep.is_installed,
                        "version": dep.installed_version,
                        "satisfied": dep.meets_requirement,
                        "error": dep.error,
                    }
                    for dep in result.dependencies
                ]

            report["plugin_details"].append(plugin_detail)

        return report
