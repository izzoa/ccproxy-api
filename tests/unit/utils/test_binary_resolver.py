"""Unit tests for binary resolver with package manager fallback."""

from unittest.mock import MagicMock, patch

import pytest

from ccproxy.config.binary import BinarySettings
from ccproxy.utils.binary_resolver import (
    BinaryResolver,
    find_binary_with_fallback,
    is_package_manager_command,
)


class TestBinaryResolver:
    """Test BinaryResolver class."""

    def test_init_default(self):
        """Test default initialization."""
        resolver = BinaryResolver()
        assert resolver.fallback_enabled is True
        assert resolver.preferred_package_manager is None
        assert resolver.package_manager_priority == ["bunx", "pnpm", "npx"]

    def test_init_custom(self):
        """Test custom initialization."""
        resolver = BinaryResolver(
            fallback_enabled=False,
            preferred_package_manager="npx",
            package_manager_priority=["npx", "bunx"],
        )
        assert resolver.fallback_enabled is False
        assert resolver.preferred_package_manager == "npx"
        assert resolver.package_manager_priority == ["npx", "bunx"]

    @patch("shutil.which")
    def test_find_binary_direct_path(self, mock_which):
        """Test finding binary directly in PATH."""
        mock_which.return_value = "/usr/local/bin/claude"
        resolver = BinaryResolver()

        result = resolver.find_binary("claude")

        assert result is not None
        assert result.command == ["/usr/local/bin/claude"]
        assert result.is_direct is True
        assert result.package_manager is None
        mock_which.assert_called_once_with("claude")

    @patch("shutil.which")
    def test_find_binary_not_found_no_fallback(self, mock_which):
        """Test binary not found with fallback disabled."""
        mock_which.return_value = None
        resolver = BinaryResolver(fallback_enabled=False)

        result = resolver.find_binary("claude")

        assert result is None
        mock_which.assert_called_once_with("claude")

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_find_binary_with_npx_fallback(self, mock_which, mock_run):
        """Test finding binary via npx fallback when it's the only available manager."""
        mock_which.return_value = None

        # Mock package manager availability checks - only npx available
        def run_side_effect(cmd, **kwargs):
            if cmd[0] == "bun" and cmd[1] == "--version":
                return MagicMock(returncode=1)  # bunx not available
            elif cmd[0] == "pnpm" and cmd[1] == "--version":
                return MagicMock(returncode=1)  # pnpm not available
            elif cmd[0] == "npx" and cmd[1] == "--version":
                return MagicMock(returncode=0, stdout="10.2.0\n")
            return MagicMock(returncode=1)

        mock_run.side_effect = run_side_effect

        resolver = BinaryResolver()
        result = resolver.find_binary("claude", "@anthropic-ai/claude-code")

        assert result is not None
        assert result.command == ["npx", "--yes", "@anthropic-ai/claude-code"]
        assert result.is_direct is False
        assert result.package_manager == "npx"

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_find_binary_with_bunx_fallback(self, mock_which, mock_run):
        """Test finding binary via bunx fallback."""
        mock_which.return_value = None

        # Mock package manager availability checks
        def run_side_effect(cmd, **kwargs):
            if cmd[0] == "bun" and cmd[1] == "--version":
                return MagicMock(returncode=0, stdout="1.0.0\n")
            elif cmd[0] == "pnpm" and cmd[1] == "--version":
                return MagicMock(returncode=1)  # pnpm not available
            elif cmd[0] == "npx" and cmd[1] == "--version":
                return MagicMock(returncode=1)  # npx not available
            return MagicMock(returncode=1)

        mock_run.side_effect = run_side_effect

        resolver = BinaryResolver()
        result = resolver.find_binary("claude", "@anthropic-ai/claude-code")

        assert result is not None
        assert result.command == ["bunx", "@anthropic-ai/claude-code"]
        assert result.is_direct is False
        assert result.package_manager == "bunx"

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_find_binary_with_pnpm_fallback(self, mock_which, mock_run):
        """Test finding binary via pnpm dlx fallback."""
        mock_which.return_value = None

        # Mock package manager availability checks
        def run_side_effect(cmd, **kwargs):
            if cmd[0] == "bun" and cmd[1] == "--version":
                return MagicMock(returncode=1)  # bunx not available
            elif cmd[0] == "pnpm" and cmd[1] == "--version":
                return MagicMock(returncode=0, stdout="8.0.0\n")
            elif cmd[0] == "npx" and cmd[1] == "--version":
                return MagicMock(returncode=1)  # npx not available
            return MagicMock(returncode=1)

        mock_run.side_effect = run_side_effect

        resolver = BinaryResolver()
        result = resolver.find_binary("claude", "@anthropic-ai/claude-code")

        assert result is not None
        assert result.command == ["pnpm", "dlx", "@anthropic-ai/claude-code"]
        assert result.is_direct is False
        assert result.package_manager == "pnpm"

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_find_binary_with_preferred_manager(self, mock_which, mock_run):
        """Test using preferred package manager."""
        mock_which.return_value = None
        mock_run.return_value = MagicMock(returncode=0, stdout="8.0.0\n")

        resolver = BinaryResolver(preferred_package_manager="pnpm")
        result = resolver.find_binary("claude", "@anthropic-ai/claude-code")

        assert result is not None
        assert result.command == ["pnpm", "dlx", "@anthropic-ai/claude-code"]
        assert result.package_manager == "pnpm"

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_find_binary_no_package_managers_available(self, mock_which, mock_run):
        """Test when no package managers are available."""
        mock_which.return_value = None
        mock_run.return_value = MagicMock(returncode=1)  # All managers fail

        resolver = BinaryResolver()
        result = resolver.find_binary("claude", "@anthropic-ai/claude-code")

        assert result is None

    def test_find_binary_consistency(self):
        """Test that find_binary returns consistent results."""
        resolver = BinaryResolver()

        with patch("shutil.which") as mock_which:
            mock_which.return_value = "/usr/local/bin/claude"

            # Multiple calls should return the same result
            result1 = resolver.find_binary("claude")
            result2 = resolver.find_binary("claude")

            assert result1 == result2
            # Each call will check which since we removed caching
            assert mock_which.call_count == 2

    def test_clear_cache(self):
        """Test clearing available managers cache."""
        resolver = BinaryResolver()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="1.0.0\n")

            # First call to get available managers
            resolver._get_available_managers()
            first_call_count = mock_run.call_count

            # Second call should use cached managers
            resolver._get_available_managers()
            assert mock_run.call_count == first_call_count  # No additional calls

            # Clear cache
            resolver.clear_cache()

            # Third call should check again
            resolver._get_available_managers()
            assert mock_run.call_count > first_call_count  # Additional calls made

    def test_from_settings(self):
        """Test creating resolver from settings."""
        from ccproxy.config.settings import Settings

        settings = Settings()
        settings.binary = BinarySettings(
            fallback_enabled=False,
            preferred_package_manager="bunx",
            package_manager_priority=["bunx", "npx"],
        )

        resolver = BinaryResolver.from_settings(settings)

        assert resolver.fallback_enabled is False
        assert resolver.preferred_package_manager == "bunx"
        assert resolver.package_manager_priority == ["bunx", "npx"]


class TestHelperFunctions:
    """Test helper functions."""

    @patch("shutil.which")
    def test_find_binary_with_fallback(self, mock_which):
        """Test convenience function."""
        mock_which.return_value = "/usr/local/bin/claude"

        result = find_binary_with_fallback("claude")

        assert result == ["/usr/local/bin/claude"]

    @patch("shutil.which")
    def test_find_binary_with_fallback_not_found(self, mock_which):
        """Test convenience function when binary not found."""
        mock_which.return_value = None

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)  # No managers available

            result = find_binary_with_fallback("claude", fallback_enabled=False)

            assert result is None

    def test_is_package_manager_command(self):
        """Test package manager command detection."""
        assert is_package_manager_command(["npx", "claude"]) is True
        assert is_package_manager_command(["bunx", "@anthropic-ai/claude-code"]) is True
        assert is_package_manager_command(["pnpm", "dlx", "claude"]) is True
        assert is_package_manager_command(["/usr/local/bin/claude"]) is False
        assert is_package_manager_command(["claude"]) is False
        assert is_package_manager_command([]) is False
        assert is_package_manager_command(None) is False  # type: ignore


class TestBinarySettings:
    """Test BinarySettings configuration."""

    def test_default_settings(self):
        """Test default binary settings."""
        settings = BinarySettings()
        assert settings.fallback_enabled is True
        assert settings.preferred_package_manager is None
        assert settings.package_manager_priority == ["bunx", "pnpm", "npx"]
        assert settings.cache_results is True

    def test_custom_settings(self):
        """Test custom binary settings."""
        settings = BinarySettings(
            fallback_enabled=False,
            preferred_package_manager="npx",
            package_manager_priority=["npx", "bunx"],
            cache_results=False,
        )
        assert settings.fallback_enabled is False
        assert settings.preferred_package_manager == "npx"
        assert settings.package_manager_priority == ["npx", "bunx"]
        assert settings.cache_results is False

    def test_invalid_preferred_manager(self):
        """Test validation of preferred package manager."""
        with pytest.raises(ValueError, match="Invalid package manager"):
            BinarySettings(preferred_package_manager="invalid")

    def test_invalid_priority_manager(self):
        """Test validation of package manager priority."""
        with pytest.raises(
            ValueError, match="Invalid package manager in priority list"
        ):
            BinarySettings(package_manager_priority=["npx", "invalid"])

    def test_duplicate_removal_in_priority(self):
        """Test that duplicates are removed from priority list."""
        settings = BinarySettings(
            package_manager_priority=["npx", "bunx", "npx", "pnpm"]
        )
        assert settings.package_manager_priority == ["npx", "bunx", "pnpm"]
