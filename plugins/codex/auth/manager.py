"""OpenAI/Codex token manager implementation for the Codex plugin."""

from datetime import datetime
from pathlib import Path
from typing import Any

from ccproxy.auth.managers.base import BaseTokenManager
from ccproxy.auth.models import (
    AccountInfo,
    OpenAICredentials,
    UserProfile,
)
from ccproxy.auth.storage.generic import GenericJsonStorage
from ccproxy.core.logging import get_logger

from .models import OpenAIProfileInfo, OpenAITokenWrapper


logger = get_logger(__name__)


class CodexTokenManager(BaseTokenManager[OpenAICredentials]):
    """Manager for Codex/OpenAI token storage and operations.

    Uses the generic storage and wrapper pattern for consistency.
    """

    def __init__(
        self,
        storage: GenericJsonStorage[OpenAICredentials] | None = None,
        oauth_client: Any = None,
    ):
        """Initialize Codex token manager.

        Args:
            storage: Optional custom storage, defaults to standard location
            oauth_client: Optional OAuth client for automatic token refresh
        """
        if storage is None:
            storage = GenericJsonStorage(
                Path.home() / ".ccproxy" / "openai_credentials.json", OpenAICredentials
            )
        super().__init__(storage)
        self._profile_cache: OpenAIProfileInfo | None = None
        self._oauth_client = oauth_client

    # ==================== Abstract Method Implementations ====================

    async def refresh_token(self, oauth_client: Any) -> OpenAICredentials | None:
        """Refresh the access token using the refresh token.

        Args:
            oauth_client: OpenAI OAuth client for token refresh

        Returns:
            Updated credentials or None if refresh failed
        """
        credentials = await self.load_credentials()
        if not credentials:
            logger.error("No credentials to refresh", category="auth")
            return None

        if not credentials.refresh_token:
            logger.error("No refresh token available", category="auth")
            return None

        try:
            # Use OAuth client to refresh - check if it has the method we need
            new_credentials: OpenAICredentials
            if hasattr(oauth_client, "refresh_access_token"):
                # OAuth provider interface
                new_credentials = await oauth_client.refresh_access_token(
                    credentials.refresh_token
                )
            else:
                # OAuth client interface
                new_credentials = await oauth_client.refresh_token(
                    credentials.refresh_token
                )

            # Preserve account_id if not in new credentials
            if not new_credentials.account_id and credentials.account_id:
                new_credentials.account_id = credentials.account_id

            # Save updated credentials
            if await self.save_credentials(new_credentials):
                logger.info(
                    "Token refreshed successfully",
                    account_id=new_credentials.account_id,
                    category="auth",
                )
                # Clear profile cache as token changed
                self._profile_cache = None
                return new_credentials

            logger.error("Failed to save refreshed credentials", category="auth")
            return None

        except Exception as e:
            logger.error(
                "Token refresh failed",
                error=str(e),
                exc_info=e,
                category="auth",
            )
            return None

    def is_expired(self, credentials: OpenAICredentials) -> bool:
        """Check if credentials are expired using wrapper."""
        wrapper = OpenAITokenWrapper(credentials=credentials)
        return wrapper.is_expired

    def get_account_id(self, credentials: OpenAICredentials) -> str | None:
        """Get account ID from credentials."""
        return credentials.account_id

    def get_expiration_time(self, credentials: OpenAICredentials) -> datetime | None:
        """Get expiration time as datetime."""
        return credentials.expires_at

    # ==================== OpenAI-Specific Methods ====================

    async def get_profile(self) -> OpenAIProfileInfo | None:
        """Get user profile from JWT token.

        OpenAI doesn't provide a profile API, so we extract
        all information from the JWT token claims.

        Returns:
            OpenAIProfileInfo or None if not authenticated
        """
        if self._profile_cache:
            return self._profile_cache

        credentials = await self.load_credentials()
        if not credentials or self.is_expired(credentials):
            return None

        # Extract profile from JWT token claims
        self._profile_cache = OpenAIProfileInfo.from_token(credentials)
        return self._profile_cache

    async def get_access_token_with_refresh(
        self, oauth_client: Any = None
    ) -> str | None:
        """Get valid access token, automatically refreshing if expired.

        Args:
            oauth_client: Optional OAuth client for token refresh

        Returns:
            Access token if available and valid, None otherwise
        """
        credentials = await self.load_credentials()
        if not credentials:
            logger.debug("No credentials found", category="auth")
            return None

        # Check if token is expired
        if self.is_expired(credentials):
            logger.info("OpenAI token is expired, attempting refresh", category="auth")

            # Use provided oauth_client or fallback to stored one
            client = oauth_client or self._oauth_client

            # Try to refresh if we have a refresh token and oauth client
            if client and credentials.refresh_token:
                try:
                    refreshed = await self.refresh_token(client)
                    if refreshed:
                        logger.info(
                            "OpenAI token refreshed successfully", category="auth"
                        )
                        return refreshed.access_token
                    else:
                        logger.error("OpenAI token refresh failed", category="auth")
                        return None
                except Exception as e:
                    logger.error(
                        "Error refreshing OpenAI token", error=str(e), category="auth"
                    )
                    return None
            else:
                logger.warning(
                    "Cannot refresh OpenAI token",
                    has_oauth_client=bool(oauth_client),
                    has_refresh_token=bool(credentials.refresh_token),
                    category="auth",
                )
                return None

        # Token is still valid
        return credentials.access_token

    async def get_access_token(self) -> str | None:
        """Override base method to return token even if expired.

        Will attempt refresh if expired but still returns the token
        even if refresh fails, letting the API handle authorization.

        Returns:
            Access token if available (expired or not), None only if no credentials
        """
        credentials = await self.load_credentials()
        if not credentials:
            logger.debug("No credentials found", category="auth")
            return None

        # Check if token is expired
        if self.is_expired(credentials):
            logger.warning(
                "OpenAI token is expired. Will attempt refresh but continue with expired token if needed.",
                category="auth",
            )

            # Try to refresh if we have OAuth client
            client = self._oauth_client
            if client and credentials.refresh_token:
                try:
                    refreshed = await self.refresh_token(client)
                    if refreshed:
                        logger.info(
                            "OpenAI token refreshed successfully", category="auth"
                        )
                        return refreshed.access_token
                    else:
                        logger.warning(
                            "OpenAI token refresh failed, using expired token",
                            category="auth",
                        )
                except Exception as e:
                    logger.warning(
                        f"Error refreshing OpenAI token, using expired token: {e}",
                        category="auth",
                    )
            else:
                logger.warning(
                    "Cannot refresh expired OpenAI token (no OAuth client or refresh token), using expired token",
                    category="auth",
                )

        # Return the token regardless of expiration status
        return credentials.access_token

    async def get_legacy_profile(self) -> UserProfile | None:
        """Get user profile in legacy format for backward compatibility.

        This converts OpenAI credentials to a UserProfile for
        compatibility with the common auth interface.
        """
        credentials = await self.load_credentials()
        if not credentials:
            return None

        # Create minimal account info with what we have
        account = AccountInfo(
            uuid=credentials.account_id,
            email="",  # OpenAI doesn't expose email in tokens
            full_name=None,
            display_name=None,
        )

        return UserProfile(
            organization=None,  # OpenAI doesn't provide org info in tokens
            account=account,
        )
