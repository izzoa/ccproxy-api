"""Enhanced base token manager with automatic token refresh."""

from ccproxy.auth.managers.base import BaseTokenManager, CredentialsT
from ccproxy.core.logging import get_logger


logger = get_logger(__name__)


class EnhancedTokenManager(BaseTokenManager[CredentialsT]):
    """Enhanced token manager with automatic refresh capability."""

    async def get_access_token_with_refresh(self) -> str | None:
        """Get valid access token, automatically refreshing if expired.

        Returns:
            Access token if available and valid, None otherwise
        """
        credentials = await self.load_credentials()
        if not credentials:
            logger.debug("no_credentials_found")
            return None

        # Check if token is expired
        if self.is_expired(credentials):
            logger.info("token_expired_attempting_refresh")

            refreshed = await self.refresh_token()
            if refreshed:
                logger.info("token_refreshed_successfully")
                credentials = refreshed
            else:
                logger.error("token_refresh_failed")
                return None

        snapshot = self._safe_token_snapshot(credentials)
        if snapshot and snapshot.access_token:
            return snapshot.access_token

        return None

    async def ensure_valid_token(self) -> bool:
        """Ensure we have a valid (non-expired) token, refreshing if needed.

        Returns:
            True if we have a valid token (after refresh if needed), False otherwise
        """
        token = await self.get_access_token_with_refresh()
        return token is not None
