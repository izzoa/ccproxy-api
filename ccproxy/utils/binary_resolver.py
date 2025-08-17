"""Binary resolution with package manager fallback support."""

import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, TypedDict

import structlog


if TYPE_CHECKING:
    from ccproxy.config.settings import Settings

logger = structlog.get_logger(__name__)


class BinaryCommand(NamedTuple):
    """Represents a resolved binary command."""

    command: list[str]
    is_direct: bool
    package_manager: str | None = None


class PackageManagerConfig(TypedDict, total=False):
    """Configuration for a package manager."""

    check_cmd: list[str]
    priority: int
    exec_cmd: str  # Optional field


class BinaryResolver:
    """Resolves binaries with fallback to package managers."""

    PACKAGE_MANAGERS: dict[str, PackageManagerConfig] = {
        "bunx": {"check_cmd": ["bun", "--version"], "priority": 1},
        "pnpm": {"check_cmd": ["pnpm", "--version"], "exec_cmd": "dlx", "priority": 2},
        "npx": {"check_cmd": ["npx", "--version"], "priority": 3},
    }

    KNOWN_PACKAGES = {
        "claude": "@anthropic-ai/claude-code",
        "codex": "@anthropic-ai/codex",
    }

    def __init__(
        self,
        fallback_enabled: bool = True,
        preferred_package_manager: str | None = None,
        package_manager_priority: list[str] | None = None,
    ):
        """Initialize the binary resolver.

        Args:
            fallback_enabled: Whether to use package manager fallback
            preferred_package_manager: Preferred package manager (bunx, pnpm, npx)
            package_manager_priority: Custom priority order for package managers
        """
        self.fallback_enabled = fallback_enabled
        self.preferred_package_manager = preferred_package_manager
        self.package_manager_priority = package_manager_priority or [
            "bunx",
            "pnpm",
            "npx",
        ]
        self._available_managers: dict[str, bool] | None = None

    def find_binary(
        self, binary_name: str, package_name: str | None = None
    ) -> BinaryCommand | None:
        """Find a binary with optional package manager fallback.

        Args:
            binary_name: Name of the binary to find
            package_name: NPM package name if different from binary name

        Returns:
            BinaryCommand with resolved command or None if not found
        """
        # First, try direct binary lookup
        direct_path = shutil.which(binary_name)
        if direct_path:
            logger.debug("binary_found_directly", binary=binary_name, path=direct_path)
            return BinaryCommand(command=[direct_path], is_direct=True)

        # If fallback is disabled, stop here
        if not self.fallback_enabled:
            logger.debug("binary_fallback_disabled", binary=binary_name)
            return None

        # Try package manager fallback
        package_name = package_name or self.KNOWN_PACKAGES.get(binary_name, binary_name)
        return self._find_via_package_manager(binary_name, package_name)

    def _find_via_package_manager(
        self, binary_name: str, package_name: str
    ) -> BinaryCommand | None:
        """Find binary via package manager execution.

        Args:
            binary_name: Name of the binary
            package_name: NPM package name

        Returns:
            BinaryCommand with package manager command or None
        """
        # Get available package managers
        available = self._get_available_managers()

        # If preferred manager is set and available, try it first
        if (
            self.preferred_package_manager
            and self.preferred_package_manager in available
        ):
            cmd = self._build_package_manager_command(
                self.preferred_package_manager, package_name
            )
            if cmd:
                logger.debug(
                    "binary_using_preferred_manager",
                    binary=binary_name,
                    manager=self.preferred_package_manager,
                    command=cmd,
                )
                return BinaryCommand(
                    command=cmd,
                    is_direct=False,
                    package_manager=self.preferred_package_manager,
                )

        # Try package managers in priority order
        for manager_name in self.package_manager_priority:
            if manager_name not in available or not available[manager_name]:
                continue

            cmd = self._build_package_manager_command(manager_name, package_name)
            if cmd:
                logger.debug(
                    "binary_using_package_manager",
                    binary=binary_name,
                    manager=manager_name,
                    command=cmd,
                )
                return BinaryCommand(
                    command=cmd, is_direct=False, package_manager=manager_name
                )

        logger.debug(
            "binary_not_found_with_fallback",
            binary=binary_name,
            package=package_name,
            available_managers=list(available.keys()),
        )
        return None

    def _build_package_manager_command(
        self, manager_name: str, package_name: str
    ) -> list[str] | None:
        """Build command for executing via package manager.

        Args:
            manager_name: Name of the package manager
            package_name: Package to execute

        Returns:
            Command list or None if manager not configured
        """
        commands = {
            "bunx": ["bunx", package_name],
            "pnpm": ["pnpm", "dlx", package_name],
            "npx": ["npx", "--yes", package_name],
        }
        return commands.get(manager_name)

    def _get_available_managers(self) -> dict[str, bool]:
        """Get available package managers on the system.

        Returns:
            Dictionary of manager names to availability status
        """
        if self._available_managers is not None:
            return self._available_managers

        self._available_managers = {}

        for manager_name, config in self.PACKAGE_MANAGERS.items():
            check_cmd = config["check_cmd"]
            try:
                # Use subprocess.run with capture to check availability
                result = subprocess.run(
                    check_cmd,
                    capture_output=True,
                    text=True,
                    timeout=2,
                    check=False,
                )
                available = result.returncode == 0
                self._available_managers[manager_name] = available
                if available:
                    logger.debug(
                        "package_manager_available",
                        manager=manager_name,
                        version=result.stdout.strip() if result.stdout else "unknown",
                    )
            except (subprocess.TimeoutExpired, FileNotFoundError):
                self._available_managers[manager_name] = False
                logger.debug("package_manager_not_available", manager=manager_name)

        return self._available_managers

    def clear_cache(self) -> None:
        """Clear all caches."""
        # Reset the available managers cache
        self._available_managers = None

    @classmethod
    def from_settings(cls, settings: "Settings") -> "BinaryResolver":
        """Create a BinaryResolver from application settings.

        Args:
            settings: Application settings

        Returns:
            Configured BinaryResolver instance
        """
        return cls(
            fallback_enabled=settings.binary.fallback_enabled,
            preferred_package_manager=settings.binary.preferred_package_manager,
            package_manager_priority=settings.binary.package_manager_priority,
        )


# Global instance for convenience
_default_resolver = BinaryResolver()


def find_binary_with_fallback(
    binary_name: str,
    package_name: str | None = None,
    fallback_enabled: bool = True,
) -> list[str] | None:
    """Convenience function to find a binary with package manager fallback.

    Args:
        binary_name: Name of the binary to find
        package_name: NPM package name if different from binary name
        fallback_enabled: Whether to use package manager fallback

    Returns:
        Command list to execute the binary, or None if not found
    """
    resolver = BinaryResolver(fallback_enabled=fallback_enabled)
    result = resolver.find_binary(binary_name, package_name)
    return result.command if result else None


def is_package_manager_command(command: list[str]) -> bool:
    """Check if a command uses a package manager.

    Args:
        command: Command list to check

    Returns:
        True if command uses a package manager
    """
    if not command:
        return False
    first_cmd = Path(command[0]).name
    return first_cmd in ["npx", "bunx", "pnpm"]
