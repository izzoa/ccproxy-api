"""Claude API token manager implementation for the Claude API plugin."""

from datetime import datetime
from pathlib import Path
from typing import Any

from ccproxy.auth.managers.base import BaseTokenManager
from ccproxy.auth.models import ClaudeCredentials
from ccproxy.auth.storage.generic import GenericJsonStorage
from ccproxy.core.logging import get_logger

from .models import ClaudeProfileInfo, ClaudeTokenWrapper


logger = get_logger(__name__)


class ClaudeApiTokenManager(BaseTokenManager[ClaudeCredentials]):
    """Manager for Claude API token storage and refresh operations.

    Uses the generic storage and wrapper pattern for consistency.
    """

    def __init__(self, storage: GenericJsonStorage[ClaudeCredentials] | None = None):
        """Initialize Claude API token manager.

        Args:
            storage: Optional custom storage, defaults to standard location
        """
        if storage is None:
            storage = GenericJsonStorage(
                Path.home() / ".claude" / ".credentials.json", ClaudeCredentials
            )
        super().__init__(storage)
        self._profile_cache: ClaudeProfileInfo | None = None

    # ==================== Abstract Method Implementations ====================

    async def refresh_token(self, oauth_client: Any) -> ClaudeCredentials | None:
        """Refresh the access token using the refresh token.

        Args:
            oauth_client: Claude OAuth client for token refresh

        Returns:
            Updated credentials or None if refresh failed
        """
        credentials = await self.load_credentials()
        if not credentials:
            logger.error("No credentials to refresh")
            return None

        wrapper = ClaudeTokenWrapper(credentials=credentials)
        refresh_token = wrapper.refresh_token_value
        if not refresh_token:
            logger.error("No refresh token available")
            return None

        try:
            # Use OAuth client to refresh
            new_credentials: ClaudeCredentials = await oauth_client.refresh_token(
                refresh_token
            )

            # Save updated credentials
            if await self.save_credentials(new_credentials):
                logger.info("Token refreshed successfully")
                # Clear profile cache as token changed
                self._profile_cache = None
                return new_credentials

            logger.error("Failed to save refreshed credentials")
            return None

        except Exception as e:
            logger.error(
                "Token refresh failed",
                error=str(e),
                exc_info=e,
            )
            return None

    def is_expired(self, credentials: ClaudeCredentials) -> bool:
        """Check if credentials are expired using wrapper."""
        wrapper = ClaudeTokenWrapper(credentials=credentials)
        return wrapper.is_expired

    def get_account_id(self, credentials: ClaudeCredentials) -> str | None:
        """Get account ID from credentials.

        Claude doesn't store account_id in tokens, would need
        to fetch from profile API.
        """
        if self._profile_cache:
            return self._profile_cache.account_id
        return None

    # ==================== Claude-Specific Methods ====================

    def get_expiration_time(self, credentials: ClaudeCredentials) -> datetime | None:
        """Get expiration time as datetime."""
        wrapper = ClaudeTokenWrapper(credentials=credentials)
        return wrapper.expires_at_datetime

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

        wrapper = ClaudeTokenWrapper(credentials=credentials)
        return wrapper.access_token_value

    async def get_profile(self) -> ClaudeProfileInfo | None:
        """Get user profile from cache or API.

        Returns:
            ClaudeProfileInfo or None if not authenticated
        """
        if self._profile_cache:
            return self._profile_cache

        credentials = await self.load_credentials()
        if not credentials or self.is_expired(credentials):
            return None

        # Would need HTTP client injected to fetch from API
        # For now, return None (actual implementation would call API)
        # response = await self.http_client.get("/api/organizations/me")
        # self._profile_cache = ClaudeProfileInfo.from_api_response(response)
        return self._profile_cache
