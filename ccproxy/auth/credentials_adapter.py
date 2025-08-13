"""Adapter to make CredentialsManager compatible with AuthManager interface."""

from typing import Any

from ccproxy.auth.exceptions import (
    AuthenticationError,
    CredentialsError,
    CredentialsExpiredError,
    CredentialsNotFoundError,
)
from ccproxy.auth.models import ClaudeCredentials, UserProfile
from ccproxy.services.credentials.manager import CredentialsManager


class CredentialsAuthManager:
    """Adapter to make CredentialsManager compatible with AuthManager interface."""

    def __init__(self, credentials_manager: CredentialsManager | None = None) -> None:
        """Initialize with credentials manager.

        Args:
            credentials_manager: CredentialsManager instance, creates new if None
        """
        self._credentials_manager = credentials_manager or CredentialsManager()

    async def get_access_token(self) -> str:
        """Get valid access token from credentials manager.

        Returns:
            Access token string

        Raises:
            AuthenticationError: If authentication fails
        """
        try:
            return await self._credentials_manager.get_access_token()
        except CredentialsNotFoundError as e:
            raise AuthenticationError("No credentials found") from e
        except CredentialsExpiredError as e:
            raise AuthenticationError("Credentials expired") from e
        except CredentialsError as e:
            raise AuthenticationError(f"Credentials error: {e}") from e

    async def get_credentials(self) -> ClaudeCredentials:
        """Get valid credentials from credentials manager.

        Returns:
            Valid credentials

        Raises:
            AuthenticationError: If authentication fails
        """
        try:
            return await self._credentials_manager.get_valid_credentials()
        except CredentialsNotFoundError as e:
            raise AuthenticationError("No credentials found") from e
        except CredentialsExpiredError as e:
            raise AuthenticationError("Credentials expired") from e
        except CredentialsError as e:
            raise AuthenticationError(f"Credentials error: {e}") from e

    async def is_authenticated(self) -> bool:
        """Check if current authentication is valid.

        Returns:
            True if authenticated, False otherwise
        """
        try:
            await self._credentials_manager.get_valid_credentials()
            return True
        except CredentialsError:
            return False

    async def get_user_profile(self) -> UserProfile | None:
        """Get user profile information.

        Returns:
            UserProfile if available, None otherwise
        """
        try:
            return await self._credentials_manager.fetch_user_profile()
        except CredentialsError:
            return None

    async def __aenter__(self) -> "CredentialsAuthManager":
        """Async context manager entry."""
        await self._credentials_manager.__aenter__()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self._credentials_manager.__aexit__(exc_type, exc_val, exc_tb)

    # ==================== Provider-Generic Methods ====================

    async def get_auth_headers(self) -> dict[str, str]:
        """Get authentication headers for the request.

        Returns:
            Dictionary of headers to add to requests
        """
        try:
            # Delegate to the credentials manager which implements this method
            return await self._credentials_manager.get_auth_headers()
        except CredentialsError as e:
            raise AuthenticationError(f"Failed to get auth headers: {e}") from e

    async def validate_credentials(self) -> bool:
        """Validate that credentials are available and valid.

        Returns:
            True if credentials are valid, False otherwise
        """
        try:
            # Delegate to the credentials manager which implements this method
            return await self._credentials_manager.validate_credentials()
        except CredentialsError:
            return False

    def get_provider_name(self) -> str:
        """Get the provider name for logging.

        Returns:
            Provider name string
        """
        # Delegate to the credentials manager which implements this method
        return self._credentials_manager.get_provider_name()
