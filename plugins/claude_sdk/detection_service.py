"""Claude SDK CLI detection service."""

import asyncio
import shutil
from pathlib import Path
from typing import NamedTuple

import structlog

from ccproxy.config.settings import Settings
from ccproxy.utils.binary_resolver import BinaryResolver


logger = structlog.get_logger(__name__)


class ClaudeDetectionData(NamedTuple):
    """Detection data for Claude CLI."""

    claude_version: str | None
    cli_path: str | list[str] | None
    is_available: bool


class ClaudeSDKDetectionService:
    """Service for detecting Claude CLI availability and configuration."""

    def __init__(self, settings: Settings) -> None:
        """Initialize the Claude SDK detection service.

        Args:
            settings: Application settings
        """
        self.settings = settings
        self._version: str | None = None
        self._cli_path: str | list[str] | None = None
        self._is_available = False

    async def initialize_detection(self) -> ClaudeDetectionData:
        """Initialize Claude CLI detection.

        Returns:
            ClaudeDetectionData with detection results
        """
        logger.info("claude_sdk_detection_starting")

        # Try to find Claude CLI
        self._cli_path = await self._find_claude_cli()

        if self._cli_path:
            self._version = await self._get_claude_version(self._cli_path)
            self._is_available = True
            logger.info(
                "claude_sdk_detection_success",
                cli_path=self._cli_path,
                version=self._version,
            )
        else:
            self._is_available = False
            logger.warning(
                "claude_sdk_detection_failed", message="Claude CLI not found"
            )

        return ClaudeDetectionData(
            claude_version=self._version,
            cli_path=self._cli_path,
            is_available=self._is_available,
        )

    async def _find_claude_cli(self) -> str | list[str] | None:
        """Find Claude CLI in common locations or via package manager.

        Returns:
            Path to Claude CLI or command list if found, None otherwise
        """
        # Check common installation locations
        claude_paths = [
            # Check if it's in PATH
            shutil.which("claude"),
            # Check common npm/bun installation locations
            Path.home() / ".cache" / ".bun" / "bin" / "claude",
            Path.home() / ".local" / "bin" / "claude",
            Path.home() / ".npm-global" / "bin" / "claude",
            # Check system locations
            Path("/usr/local/bin/claude"),
            Path("/usr/bin/claude"),
        ]

        for path in claude_paths:
            if path and Path(str(path)).exists():
                return str(path)

        # Try package manager fallback
        resolver = BinaryResolver()
        result = resolver.find_binary("claude", "@anthropic-ai/claude-code")
        if result:
            # If it's a package manager command, return the command list
            if not result.is_direct:
                return result.command
            # Otherwise return the direct path
            return result.command[0] if result.command else None

        return None

    async def _get_claude_version(self, cli_path: str | list[str]) -> str | None:
        """Get Claude CLI version.

        Args:
            cli_path: Path to Claude CLI executable or command list

        Returns:
            Version string if successful, None otherwise
        """
        try:
            # Prepare command based on input type
            if isinstance(cli_path, list):
                # It's a package manager command
                cmd = cli_path + ["--version"]
            else:
                # It's a direct path
                cmd = [cli_path, "--version"]

            # Run claude --version with timeout
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10.0)

            if process.returncode == 0 and stdout:
                version_output = stdout.decode().strip()
                # Extract version from output (usually format: "Claude CLI x.y.z")
                if "Claude CLI" in version_output:
                    return version_output.split("Claude CLI")[-1].strip()
                return version_output
            else:
                logger.warning(
                    "claude_sdk_version_command_failed",
                    returncode=process.returncode,
                    stderr=stderr.decode() if stderr else None,
                )
                return None

        except TimeoutError:
            logger.warning("claude_sdk_version_timeout", cli_path=str(cli_path))
            return None
        except Exception as e:
            logger.warning(
                "claude_sdk_version_error",
                cli_path=str(cli_path),
                error=str(e),
            )
            return None

    def get_version(self) -> str | None:
        """Get the detected Claude CLI version.

        Returns:
            Version string if available, None otherwise
        """
        return self._version

    def get_cli_path(self) -> str | list[str] | None:
        """Get the detected Claude CLI path or command.

        Returns:
            CLI path or command list if available, None otherwise
        """
        return self._cli_path

    def is_claude_available(self) -> bool:
        """Check if Claude CLI is available.

        Returns:
            True if Claude CLI was detected, False otherwise
        """
        return self._is_available
