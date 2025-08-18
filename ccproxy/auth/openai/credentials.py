"""OpenAI credentials management for Codex authentication."""

from datetime import UTC, datetime
from typing import Any

import jwt
import structlog
from pydantic import BaseModel, Field, field_validator

from ccproxy.auth.exceptions import AuthenticationError
from ccproxy.auth.models import ClaudeCredentials, OAuthToken, UserProfile

from .storage import OpenAITokenStorage


logger = structlog.get_logger(__name__)


class OpenAICredentials(BaseModel):
    """OpenAI authentication credentials model."""

    access_token: str = Field(..., description="OpenAI access token (JWT)")
    refresh_token: str = Field(..., description="OpenAI refresh token")
    expires_at: datetime = Field(..., description="Token expiration timestamp")
    account_id: str = Field(..., description="OpenAI account ID extracted from token")
    active: bool = Field(default=True, description="Whether credentials are active")

    @field_validator("expires_at", mode="before")
    @classmethod
    def parse_expires_at(cls, v: Any) -> datetime:
        """Parse expiration timestamp."""
        if isinstance(v, datetime):
            # Ensure timezone-aware datetime
            if v.tzinfo is None:
                return v.replace(tzinfo=UTC)
            return v

        if isinstance(v, str):
            # Handle ISO format strings
            try:
                dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                return dt
            except ValueError as e:
                raise ValueError(f"Invalid datetime format: {v}") from e

        if isinstance(v, int | float):
            # Handle Unix timestamps
            return datetime.fromtimestamp(v, tz=UTC)

        raise ValueError(f"Cannot parse datetime from {type(v)}: {v}")

    @field_validator("account_id", mode="before")
    @classmethod
    def extract_account_id(cls, v: Any, info: Any) -> str:
        """Extract account ID from access token if not provided."""
        if isinstance(v, str) and v:
            return v

        # Try to extract from access_token
        access_token = None
        if hasattr(info, "data") and info.data and isinstance(info.data, dict):
            access_token = info.data.get("access_token")

        if access_token and isinstance(access_token, str):
            try:
                # Decode JWT without verification to extract claims
                decoded = jwt.decode(access_token, options={"verify_signature": False})
                if "org_id" in decoded and isinstance(decoded["org_id"], str):
                    return decoded["org_id"]
                elif "sub" in decoded and isinstance(decoded["sub"], str):
                    return decoded["sub"]
                elif "account_id" in decoded and isinstance(decoded["account_id"], str):
                    return decoded["account_id"]
            except Exception as e:
                logger.warning("Failed to extract account_id from token", error=str(e))

        raise ValueError(
            "account_id is required and could not be extracted from access_token"
        )

    def is_expired(self) -> bool:
        """Check if the access token is expired."""
        now = datetime.now(UTC)
        return now >= self.expires_at

    def expires_in_seconds(self) -> int:
        """Get seconds until token expires."""
        now = datetime.now(UTC)
        delta = self.expires_at - now
        return max(0, int(delta.total_seconds()))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at.isoformat(),
            "account_id": self.account_id,
            "active": self.active,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OpenAICredentials":
        """Create from dictionary."""
        return cls(**data)


class OpenAITokenManager:
    """Manages OpenAI token storage and refresh operations."""

    def __init__(self, storage: OpenAITokenStorage | None = None):
        """Initialize token manager.

        Args:
            storage: Token storage backend. If None, uses default TOML file storage.
            device_id: Optional device ID for OpenAI requests.
        """
        self.storage = storage or OpenAITokenStorage()

    async def load_credentials(self) -> OpenAICredentials | None:
        """Load credentials from storage."""
        try:
            return await self.storage.load()
        except Exception as e:
            logger.error("Failed to load OpenAI credentials", error=str(e))
            return None

    async def save_credentials(self, credentials: OpenAICredentials) -> bool:
        """Save credentials to storage."""
        try:
            return await self.storage.save(credentials)
        except Exception as e:
            logger.error("Failed to save OpenAI credentials", error=str(e))
            return False

    async def delete_credentials(self) -> bool:
        """Delete credentials from storage."""
        try:
            return await self.storage.delete()
        except Exception as e:
            logger.error("Failed to delete OpenAI credentials", error=str(e))
            return False

    async def has_credentials(self) -> bool:
        """Check if credentials exist."""
        try:
            return await self.storage.exists()
        except Exception:
            return False

    async def get_valid_token(self) -> str | None:
        """Get a valid access token, refreshing if necessary."""
        credentials = await self.load_credentials()
        if not credentials or not credentials.active:
            return None

        # If token is not expired, return it
        if not credentials.is_expired():
            return credentials.access_token

        # TODO: Implement token refresh logic
        # For now, return None if expired (user needs to re-authenticate)
        logger.warning("OpenAI token expired, refresh not yet implemented")
        return None

    def get_storage_location(self) -> str:
        """Get storage location description."""
        return self.storage.get_location()

    # ==================== Core Authentication Methods ====================

    async def get_access_token(self) -> str:
        """Get valid access token.

        Returns:
            Access token string

        Raises:
            AuthenticationError: If authentication fails
        """
        token = await self.get_valid_token()
        if not token:
            raise AuthenticationError("No valid OpenAI token")
        return token

    async def get_credentials(self) -> ClaudeCredentials:
        """Get valid credentials.

        Note: For OpenAI providers, this returns minimal/dummy Claude credentials.

        Returns:
            Minimal Claude credentials for compatibility

        Raises:
            AuthenticationError: If authentication fails
        """
        credentials = await self.load_credentials()
        if not credentials or not credentials.active:
            raise AuthenticationError("No valid OpenAI credentials")

        # Create minimal ClaudeCredentials for compatibility
        oauth_token = OAuthToken(
            accessToken=credentials.access_token,
            refreshToken=credentials.refresh_token,
            expiresAt=int(credentials.expires_at.timestamp() * 1000),
            scopes=["openai-api"],
            subscriptionType="openai",
            tokenType="Bearer",
        )
        return ClaudeCredentials(claudeAiOauth=oauth_token)

    async def is_authenticated(self) -> bool:
        """Check if current authentication is valid.

        Returns:
            True if authenticated, False otherwise
        """
        try:
            token = await self.get_valid_token()
            return bool(token)
        except Exception:
            return False

    async def get_user_profile(self) -> UserProfile | None:
        """Get user profile information.

        Returns:
            None - OpenAI token manager doesn't support user profiles
        """
        return None

    # ==================== Context Manager Support ====================

    async def __aenter__(self) -> "OpenAITokenManager":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        pass

    # ==================== Provider-Generic Methods ====================

    async def get_auth_headers(self) -> dict[str, str]:
        """Get OpenAI auth headers."""
        token = await self.get_valid_token()
        if not token:
            raise AuthenticationError("No valid OpenAI token")
        return {
            "authorization": f"Bearer {token}",
        }

    async def validate_credentials(self) -> bool:
        """Check if we have valid OpenAI credentials."""
        try:
            token = await self.get_valid_token()
            return bool(token)
        except Exception:
            return False

    def get_provider_name(self) -> str:
        """Get the provider name for logging."""
        return "openai-codex"

    def _redact_account_id(self, account_id: str) -> str:
        """Redact account ID for privacy, keeping only the prefix.

        Args:
            account_id: Full account ID like 'auth0|xxxxxxxxxxxxx'

        Returns:
            Redacted ID like 'auth0|...'
        """
        if "|" in account_id:
            prefix = account_id.split("|")[0]
            return f"{prefix}|..."
        # If no pipe, just show first few chars
        if len(account_id) > 8:
            return account_id[:8] + "..."
        return account_id

    async def get_auth_status(self) -> dict[str, Any]:
        """Get detailed authentication status information.

        Returns:
            Dictionary with auth status details including token info,
            expiration, and storage location.
        """
        status: dict[str, Any] = {
            "auth_configured": False,
            "token_available": False,
            "storage_location": self.get_storage_location(),
        }

        try:
            # Check if credentials exist
            has_creds = await self.has_credentials()
            if not has_creds:
                return status

            status["auth_configured"] = True

            # Load credentials to get details
            credentials = await self.load_credentials()
            if not credentials:
                return status

            # Get token details
            token = credentials.access_token
            if token:
                status["token_available"] = True
                status["token_preview"] = (
                    token[:20] + "..." if len(token) > 20 else "[SHORT]"
                )

                # Decode token to get claims
                try:
                    decoded = jwt.decode(token, options={"verify_signature": False})

                    # Extract expiration and other details
                    exp_timestamp = decoded.get("exp", 0)
                    if exp_timestamp:
                        exp_dt = datetime.fromtimestamp(exp_timestamp, tz=UTC)
                        now = datetime.now(UTC)
                        time_remaining = exp_dt - now

                        days = time_remaining.days
                        hours = time_remaining.seconds // 3600
                        minutes = (time_remaining.seconds % 3600) // 60

                        status.update(
                            {
                                "token_expired": exp_dt < now,
                                "expires_at": exp_dt.isoformat(),
                                "time_remaining": (
                                    f"{days} days, {hours} hours, {minutes} minutes"
                                    if exp_dt > now
                                    else "Expired"
                                ),
                                "issuer": decoded.get("iss", "Unknown"),
                                "audience": decoded.get("aud", "Unknown"),
                                "account_id_preview": self._redact_account_id(
                                    decoded.get("org_id")
                                    or decoded.get("sub")
                                    or "Unknown"
                                ),
                            }
                        )

                    # Add account active status
                    status["account_active"] = credentials.active

                except Exception as e:
                    logger.debug("Failed to decode token for status", error=str(e))
                    # Just add basic info if decoding fails
                    status["expires_at"] = credentials.expires_at.isoformat()
                    status["token_expired"] = credentials.is_expired()
                    status["account_active"] = credentials.active

        except Exception as e:
            logger.debug("Failed to get auth status", error=str(e))
            status["auth_error"] = str(e)

        return status
