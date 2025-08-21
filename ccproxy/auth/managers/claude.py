"""Claude/Anthropic-specific token manager implementation."""

from datetime import UTC, datetime
from typing import Any

from pydantic import SecretStr

from ccproxy.auth.managers.base import BaseTokenManager
from ccproxy.auth.models import ClaudeCredentials, UserProfile
from ccproxy.auth.storage.claude import ClaudeTokenStorage
from ccproxy.core.logging import get_logger


logger = get_logger(__name__)


class ClaudeTokenManager(BaseTokenManager[ClaudeCredentials]):
    """Manager for Claude/Anthropic token storage and refresh operations."""

    def __init__(self, storage: ClaudeTokenStorage | None = None):
        """Initialize Claude token manager.

        Args:
            storage: Token storage backend. If None, uses default Claude storage.
        """
        if storage is None:
            # Use default path for Claude credentials
            from pathlib import Path
            default_path = Path.home() / ".config" / "claude" / "credentials.json"
            storage = ClaudeTokenStorage(default_path)
        super().__init__(storage)

    # ==================== Abstract Method Implementations ====================

    async def validate_token(self) -> bool:
        """Check if stored token is valid and not expired."""
        credentials = await self.load_credentials()
        if not credentials:
            return False

        if self.is_expired(credentials):
            logger.info("Token is expired")
            return False

        return True

    async def refresh_token(self, oauth_client: Any) -> ClaudeCredentials | None:
        """Refresh the access token using the refresh token.

        Args:
            oauth_client: The OAuth client to use for refreshing

        Returns:
            Updated credentials or None if refresh failed
        """
        credentials = await self.load_credentials()
        if not credentials:
            logger.error("No credentials to refresh")
            return None

        refresh_token = credentials.claude_ai_oauth.refresh_token
        if not refresh_token:
            logger.error("No refresh token available")
            return None

        try:
            # Use the OAuth client to refresh
            # Get the actual token value from SecretStr
            refresh_token_value = refresh_token.get_secret_value()
            new_credentials = await oauth_client.refresh_token(refresh_token_value)

            # Save the new credentials
            if await self.save_credentials(new_credentials):
                logger.info("Token refreshed successfully")
                return new_credentials
            else:
                logger.error("Failed to save refreshed credentials")
                return None

        except Exception as e:
            logger.error(
                "Token refresh failed",
                error=str(e),
                exc_info=e,
            )
            return None

    async def get_auth_status(self) -> dict[str, Any]:
        """Get current authentication status."""
        credentials = await self.load_credentials()

        if not credentials:
            return {
                "authenticated": False,
                "reason": "No credentials found",
            }

        if self.is_expired(credentials):
            expires_at = self._get_expiration_time(credentials)
            return {
                "authenticated": False,
                "reason": "Token expired",
                "expires_at": expires_at.isoformat() if expires_at else None,
            }

        expires_at = self._get_expiration_time(credentials)
        expires_in = self._calculate_expires_in(expires_at) if expires_at else None

        return {
            "authenticated": True,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "expires_in": expires_in,
        }

    def is_expired(self, credentials: ClaudeCredentials) -> bool:
        """Check if credentials are expired."""
        expires_at = credentials.claude_ai_oauth.expires_at
        if not expires_at:
            # No expiration time means token doesn't expire
            return False

        now = datetime.now(UTC)
        expires_time = datetime.fromtimestamp(expires_at, tz=UTC)
        return now >= expires_time

    def get_account_id(self, credentials: ClaudeCredentials) -> str | None:
        """Get account ID from credentials.

        Claude credentials don't have a direct account_id field,
        so we return None or could extract from token if needed.
        """
        # Could potentially extract from JWT token if needed
        return None

    # ==================== Claude-Specific Methods ====================

    def _get_expiration_time(self, credentials: ClaudeCredentials) -> datetime | None:
        """Get expiration time from credentials."""
        expires_at = credentials.claude_ai_oauth.expires_at
        if not expires_at:
            return None
        return datetime.fromtimestamp(expires_at, tz=UTC)

    def _calculate_expires_in(self, expires_at: datetime) -> int:
        """Calculate seconds until expiration."""
        now = datetime.now(UTC)
        delta = expires_at - now
        return max(0, int(delta.total_seconds()))

    async def get_access_token_value(self) -> str | None:
        """Get the actual access token value.

        Returns:
            Access token string if available, None otherwise
        """
        credentials = await self.load_credentials()
        if not credentials:
            return None

        if self.is_expired(credentials):
            return None

        return credentials.claude_ai_oauth.access_token.get_secret_value()

    async def get_profile(self) -> UserProfile | None:
        """Get user profile.

        Note: This would typically fetch from an API endpoint,
        but for now returns None as profile fetching is handled
        by the CredentialsManager.
        """
        # Profile fetching is typically handled by the full CredentialsManager
        # which has access to HTTP clients and API endpoints
        return None
