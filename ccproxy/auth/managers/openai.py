"""OpenAI-specific token manager implementation."""

from datetime import UTC, datetime
from typing import Any

from ccproxy.auth.managers.base import BaseTokenManager
from ccproxy.auth.models import (
    ClaudeCredentials,
    OAuthToken,
    OpenAICredentials,
    UserProfile,
)
from ccproxy.auth.storage.openai import OpenAITokenStorage
from ccproxy.core.logging import get_logger


logger = get_logger(__name__)


class OpenAITokenManager(BaseTokenManager[OpenAICredentials]):
    """Manager for OpenAI token storage and refresh operations."""

    def __init__(self, storage: OpenAITokenStorage | None = None):
        """Initialize OpenAI token manager.

        Args:
            storage: Token storage backend. If None, uses default OpenAI storage.
        """
        storage = storage or OpenAITokenStorage()
        super().__init__(storage)

    # ==================== Abstract Method Implementations ====================

    async def validate_token(self) -> bool:
        """Check if stored token is valid and not expired."""
        credentials = await self.load_credentials()
        if not credentials:
            return False

        if self.is_expired(credentials):
            logger.info("Token is expired", account_id=credentials.account_id)
            return False

        return True

    async def refresh_token(self, oauth_client: Any) -> OpenAICredentials | None:
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

        if not credentials.refresh_token:
            logger.error("No refresh token available")
            return None

        try:
            # Use the OAuth client to refresh
            new_credentials = await oauth_client.refresh_token(
                credentials.refresh_token
            )

            # Preserve the account_id if it's not in the new credentials
            if not new_credentials.account_id and credentials.account_id:
                new_credentials.account_id = credentials.account_id

            # Save the new credentials
            if await self.save_credentials(new_credentials):
                logger.info(
                    "Token refreshed successfully",
                    account_id=new_credentials.account_id,
                )
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

        if credentials.is_expired():
            return {
                "authenticated": False,
                "reason": "Token expired",
                "expires_at": credentials.expires_at.isoformat(),
                "account_id": credentials.account_id,
            }

        return {
            "authenticated": True,
            "account_id": credentials.account_id,
            "expires_at": credentials.expires_at.isoformat(),
            "expires_in": credentials.expires_in_seconds(),
        }

    def is_expired(self, credentials: OpenAICredentials) -> bool:
        """Check if credentials are expired."""
        return credentials.is_expired()

    def get_account_id(self, credentials: OpenAICredentials) -> str | None:
        """Get account ID from credentials."""
        return credentials.account_id if credentials.account_id else None

    # ==================== OpenAI-Specific Methods ====================

    async def get_profile(self) -> UserProfile | None:
        """Get user profile from stored credentials.

        This converts OpenAI credentials to a UserProfile for
        compatibility with the common auth interface.
        """
        credentials = await self.load_credentials()
        if not credentials:
            return None

        # Create a minimal UserProfile from OpenAI credentials
        # OpenAI doesn't provide all the same profile data as Claude
        # UserProfile only has organization and account fields
        from ccproxy.auth.models import AccountInfo

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

    async def convert_to_claude_format(self) -> ClaudeCredentials | None:
        """Convert OpenAI credentials to Claude format for compatibility.

        This is useful when the system expects Claude credentials
        but we're using OpenAI as the provider.
        """
        credentials = await self.load_credentials()
        if not credentials:
            return None

        # Convert to Claude format
        # Note: This is a compatibility layer and some fields may not map perfectly
        from pydantic import SecretStr

        oauth_token = OAuthToken(
            accessToken=SecretStr(credentials.access_token),
            refreshToken=SecretStr(credentials.refresh_token),
            expiresAt=int(credentials.expires_at.timestamp())
            if credentials.expires_at
            else None,
            scopes=[],  # OpenAI doesn't expose scopes in the same way
            subscriptionType=None,  # OpenAI doesn't have subscription types
        )

        return ClaudeCredentials(claudeAiOauth=oauth_token)
