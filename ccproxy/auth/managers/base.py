"""Base token manager for all authentication providers."""

import json
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from ccproxy.auth.exceptions import (
    AuthenticationError,
    CredentialsInvalidError,
    CredentialsStorageError,
)
from ccproxy.auth.storage.base import TokenStorage
from ccproxy.core.logging import get_logger
from ccproxy.utils.caching import AuthStatusCache, async_ttl_cache


logger = get_logger(__name__)

# Type variable for credentials
CredentialsT = TypeVar("CredentialsT", bound=BaseModel)


class BaseTokenManager(ABC, Generic[CredentialsT]):
    """Base manager for token storage and refresh operations.

    This generic base class provides common functionality for managing
    authentication tokens across different providers (OpenAI, Claude, etc.).

    Type Parameters:
        CredentialsT: The specific credential type (e.g., OpenAICredentials, ClaudeCredentials)
    """

    def __init__(self, storage: TokenStorage[CredentialsT]):
        """Initialize token manager.

        Args:
            storage: Token storage backend that matches the credential type
        """
        self.storage = storage
        self._auth_cache = AuthStatusCache(ttl=60.0)  # 1 minute TTL for auth status

    # ==================== Core Operations ====================

    async def load_credentials(self) -> CredentialsT | None:
        """Load credentials from storage.

        Returns:
            Credentials if found and valid, None otherwise
        """
        try:
            return await self.storage.load()
        except (OSError, PermissionError) as e:
            logger.error("storage_access_failed", error=str(e), exc_info=e)
            return None
        except (CredentialsStorageError, CredentialsInvalidError) as e:
            logger.error("credentials_load_failed", error=str(e), exc_info=e)
            return None
        except json.JSONDecodeError as e:
            logger.error("credentials_json_decode_error", error=str(e), exc_info=e)
            return None
        except ValidationError as e:
            logger.error("credentials_validation_error", error=str(e), exc_info=e)
            return None
        except Exception as e:
            logger.error("unexpected_load_error", error=str(e), exc_info=e)
            return None

    async def save_credentials(self, credentials: CredentialsT) -> bool:
        """Save credentials to storage.

        Args:
            credentials: Credentials to save

        Returns:
            True if saved successfully, False otherwise
        """
        try:
            return await self.storage.save(credentials)
        except (OSError, PermissionError) as e:
            logger.error("storage_access_failed", error=str(e), exc_info=e)
            return False
        except CredentialsStorageError as e:
            logger.error("credentials_save_failed", error=str(e), exc_info=e)
            return False
        except json.JSONDecodeError as e:
            logger.error("credentials_json_encode_error", error=str(e), exc_info=e)
            return False
        except ValidationError as e:
            logger.error("credentials_validation_error", error=str(e), exc_info=e)
            return False
        except Exception as e:
            logger.error("unexpected_save_error", error=str(e), exc_info=e)
            return False

    async def clear_credentials(self) -> bool:
        """Clear stored credentials.

        Returns:
            True if cleared successfully, False otherwise
        """
        try:
            # Clear the cache
            self._auth_cache.clear()

            # Delete from storage
            return await self.storage.delete()
        except Exception as e:
            logger.error("Failed to clear credentials", error=str(e), exc_info=e)
            return False

    def get_storage_location(self) -> str:
        """Get the storage location for credentials.

        Returns:
            Storage location description
        """
        return self.storage.get_location()

    # ==================== Abstract Methods ====================

    @abstractmethod
    async def validate_token(self) -> bool:
        """Check if stored token is valid and not expired.

        Returns:
            True if valid, False otherwise
        """
        pass

    @abstractmethod
    async def refresh_token(self, oauth_client: Any) -> CredentialsT | None:
        """Refresh the access token using the refresh token.

        Args:
            oauth_client: The OAuth client to use for refreshing

        Returns:
            Updated credentials or None if refresh failed
        """
        pass

    @abstractmethod
    async def get_auth_status(self) -> dict[str, Any]:
        """Get current authentication status.

        Returns:
            Dictionary with authentication status information
        """
        pass

    @abstractmethod
    def is_expired(self, credentials: CredentialsT) -> bool:
        """Check if credentials are expired.

        Args:
            credentials: Credentials to check

        Returns:
            True if expired, False otherwise
        """
        pass

    @abstractmethod
    def get_account_id(self, credentials: CredentialsT) -> str | None:
        """Get account ID from credentials.

        Args:
            credentials: Credentials to extract account ID from

        Returns:
            Account ID if available, None otherwise
        """
        pass

    # ==================== Common Utility Methods ====================

    async def is_authenticated(self) -> bool:
        """Check if current authentication is valid.

        Returns:
            True if authenticated, False otherwise
        """
        credentials = await self.load_credentials()
        if not credentials:
            return False

        return not self.is_expired(credentials)

    async def get_access_token(self) -> str | None:
        """Get valid access token from credentials.

        Returns:
            Access token if available and valid, None otherwise
        """
        credentials = await self.load_credentials()
        if not credentials:
            return None

        if self.is_expired(credentials):
            logger.info("Token is expired")
            return None

        # Get access_token attribute from credentials
        if hasattr(credentials, "access_token"):
            return str(credentials.access_token)
        elif hasattr(credentials, "claude_ai_oauth"):
            # Handle Claude credentials format
            return str(credentials.claude_ai_oauth.access_token.get_secret_value())

        return None

    @async_ttl_cache(ttl=60.0)  # Cache auth status for 1 minute
    async def get_cached_auth_status(self) -> dict[str, Any]:
        """Get current authentication status with caching.

        This is a convenience method that wraps get_auth_status() with caching.

        Returns:
            Dictionary with authentication status information
        """
        return await self.get_auth_status()
