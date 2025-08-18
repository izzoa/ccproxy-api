"""Claude SDK CLI detection service - simplified version."""

import asyncio
from typing import NamedTuple

import structlog

from ccproxy.config.settings import Settings
from ccproxy.utils.binary_resolver import BinaryResolver


logger = structlog.get_logger(__name__)


class ClaudeDetectionData(NamedTuple):
    """Detection data for Claude CLI."""

    claude_version: str | None
    cli_command: list[str] | None
    is_available: bool


class ClaudeSDKDetectionService:
    """Service for detecting Claude CLI availability.

    This is a simplified detection service that only checks if the Claude CLI
    exists. Unlike the Claude API plugin, this doesn't support fallback mode
    as the SDK requires the actual CLI to be present.
    """

    def __init__(self, settings: Settings) -> None:
        """Initialize the Claude SDK detection service.

        Args:
            settings: Application settings
        """
        self.settings = settings
        self._version: str | None = None
        self._cli_command: list[str] | None = None
        self._is_available = False

    async def initialize_detection(self) -> ClaudeDetectionData:
        """Initialize Claude CLI detection.

        Returns:
            ClaudeDetectionData with detection results

        Note:
            No fallback support - SDK requires actual CLI presence
        """
        logger.debug("claude_sdk_detection_starting")

        # Try to find Claude CLI
        self._cli_command = self._find_claude_cli()

        if self._cli_command:
            self._version = await self._get_claude_version(self._cli_command)
            self._is_available = True
            logger.debug(
                "claude_sdk_detection_success",
                cli_command=self._cli_command,
                version=self._version,
            )
        else:
            self._is_available = False
            logger.error(
                "claude_sdk_detection_failed",
                message="Claude CLI not found - SDK plugin cannot function without CLI",
            )

        return ClaudeDetectionData(
            claude_version=self._version,
            cli_command=self._cli_command,
            is_available=self._is_available,
        )

    def _find_claude_cli(self) -> list[str] | None:
        """Find Claude CLI using binary resolver.

        Returns:
            Command list to execute Claude CLI if found, None otherwise
        """
        try:
            # For claude_sdk, we want only installed binaries, not package manager execution
            resolver = BinaryResolver()
            result = resolver.find_binary(
                "claude",
                "@anthropic-ai/claude-code",
                package_manager_only=False,
                fallback_enabled=False,
            )
            if result and result.is_direct:
                logger.debug(
                    "claude_cli_found",
                    command=result.command,
                    is_in_path=result.is_in_path,
                )
                # Always return as command list for consistency
                return result.command
        except Exception as e:
            logger.debug("binary_resolver_failed", error=str(e), exc_info=e)

        return None

    async def _get_claude_version(self, cli_command: list[str]) -> str | None:
        """Get Claude CLI version.

        Args:
            cli_command: Command list to execute Claude CLI

        Returns:
            Version string if successful, None otherwise
        """
        try:
            # Prepare command with --version flag
            cmd = cli_command + ["--version"]

            # Run claude --version with timeout
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=5.0)

            if process.returncode == 0 and stdout:
                version_output = stdout.decode().strip()
                # Handle various version output formats
                if "/" in version_output:
                    # Handle "claude-cli/1.0.60" format
                    version_output = version_output.split("/")[-1]
                if "(" in version_output:
                    # Handle "1.0.60 (Claude Code)" format
                    version_output = version_output.split("(")[0].strip()
                return version_output

            return None

        except (TimeoutError, Exception) as e:
            logger.debug("claude_version_check_failed", error=str(e))
            return None

    def get_version(self) -> str | None:
        """Get the detected Claude CLI version.

        Returns:
            Version string if available, None otherwise
        """
        return self._version

    def get_cli_path(self) -> list[str] | None:
        """Get the detected Claude CLI command.

        Returns:
            CLI command list if available, None otherwise
        """
        return self._cli_command

    def is_claude_available(self) -> bool:
        """Check if Claude CLI is available.

        Returns:
            True if Claude CLI was detected, False otherwise
        """
        return self._is_available
