"""OpenAI credentials management for Codex authentication."""

import json
from datetime import UTC, datetime
from typing import Any

import jwt
import structlog
from pydantic import BaseModel, Field, SecretStr, ValidationError, field_validator

from ccproxy.auth.exceptions import (
    AuthenticationError,
    CredentialsInvalidError,
    CredentialsStorageError,
)
from ccproxy.auth.models import ClaudeCredentials, OAuthToken, UserProfile
from ccproxy.utils.caching import AuthStatusCache, async_ttl_cache

from .storage import OpenAITokenStorage


logger = structlog.get_logger(__name__)


class OpenAICredentials(BaseModel):
    """OpenAI authentication credentials model."""

    access_token: str = Field(..., description="OpenAI access token (JWT)")
    refresh_token: str = Field(..., description="OpenAI refresh token")
    id_token: str | None = Field(None, description="OpenAI ID token (JWT)")
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
        """Extract account ID from tokens if not provided.

        Prioritizes chatgpt_account_id (UUID format) from id_token,
        falls back to auth0 sub claim if not found.
        """
        if isinstance(v, str) and v:
            return v

        # Try to extract from id_token first (contains chatgpt_account_id UUID)
        id_token = None
        if hasattr(info, "data") and info.data and isinstance(info.data, dict):
            id_token = info.data.get("id_token")

        if id_token and isinstance(id_token, str):
            try:
                # Decode JWT without verification to extract claims
                decoded = jwt.decode(id_token, options={"verify_signature": False})

                # Look for OpenAI auth claims with chatgpt_account_id (proper UUID)
                if "https://api.openai.com/auth" in decoded:
                    auth_claims = decoded["https://api.openai.com/auth"]
                    if isinstance(auth_claims, dict):
                        # Use chatgpt_account_id if available (this is the proper UUID)
                        if "chatgpt_account_id" in auth_claims and isinstance(
                            auth_claims["chatgpt_account_id"], str
                        ):
                            account_id = auth_claims["chatgpt_account_id"]
                            logger.info(
                                "Using chatgpt_account_id from id_token",
                                account_id=account_id,
                            )
                            return account_id

                        # Also check organization_id as a fallback
                        if "organization_id" in auth_claims and isinstance(
                            auth_claims["organization_id"], str
                        ):
                            org_id = auth_claims["organization_id"]
                            if not org_id.startswith("auth0|"):
                                logger.info(
                                    "Using organization_id from id_token",
                                    org_id=org_id,
                                )
                                return org_id

                # Check top-level claims in id_token
                if "account_id" in decoded and isinstance(decoded["account_id"], str):
                    return decoded["account_id"]
                elif "org_id" in decoded and isinstance(decoded["org_id"], str):
                    # Check if org_id looks like a UUID (not auth0|xxx format)
                    org_id = decoded["org_id"]
                    if not org_id.startswith("auth0|"):
                        return org_id
            except (jwt.DecodeError, jwt.InvalidTokenError, KeyError, ValueError) as e:
                logger.debug("id_token_decode_failed", error=str(e))

        # Try to extract from access_token
        access_token = None
        if hasattr(info, "data") and info.data and isinstance(info.data, dict):
            access_token = info.data.get("access_token")

        if access_token and isinstance(access_token, str):
            try:
                # Decode JWT without verification to extract claims
                decoded = jwt.decode(access_token, options={"verify_signature": False})

                # Check for OpenAI auth claims in access_token too
                if "https://api.openai.com/auth" in decoded:
                    auth_claims = decoded["https://api.openai.com/auth"]
                    if (
                        isinstance(auth_claims, dict)
                        and "chatgpt_account_id" in auth_claims
                    ):
                        account_id = auth_claims["chatgpt_account_id"]
                        logger.info(
                            "Using chatgpt_account_id from access_token",
                            account_id=account_id,
                        )
                        return str(account_id)

                if "org_id" in decoded and isinstance(decoded["org_id"], str):
                    return decoded["org_id"]
                elif "sub" in decoded and isinstance(decoded["sub"], str):
                    # Fallback to auth0 sub (not ideal but maintains compatibility)
                    sub = decoded["sub"]
                    logger.warning(
                        "Falling back to auth0 sub as account_id - consider updating to use chatgpt_account_id",
                        sub=sub,
                    )
                    return sub
                elif "account_id" in decoded and isinstance(decoded["account_id"], str):
                    return decoded["account_id"]
            except (jwt.DecodeError, jwt.InvalidTokenError, KeyError, ValueError) as e:
                logger.warning("jwt_decode_failed", error=str(e), exc_info=e)

        raise ValueError(
            "account_id is required and could not be extracted from tokens"
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
            "id_token": self.id_token,
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
        self._auth_cache = AuthStatusCache(ttl=60.0)  # 1 minute TTL for auth status

    async def load_credentials(self) -> OpenAICredentials | None:
        """Load credentials from storage."""
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

    async def save_credentials(self, credentials: OpenAICredentials) -> bool:
        """Save credentials to storage."""
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

    async def delete_credentials(self) -> bool:
        """Delete credentials from storage."""
        try:
            return await self.storage.delete()
        except (OSError, PermissionError) as e:
            logger.error("storage_access_failed", error=str(e), exc_info=e)
            return False
        except CredentialsStorageError as e:
            logger.error("credentials_delete_failed", error=str(e), exc_info=e)
            return False
        except ValidationError as e:
            logger.error("credentials_validation_error", error=str(e), exc_info=e)
            return False
        except Exception as e:
            logger.error("unexpected_delete_error", error=str(e), exc_info=e)
            return False

    async def has_credentials(self) -> bool:
        """Check if credentials exist."""
        try:
            return await self.storage.exists()
        except (
            OSError,
            PermissionError,
            CredentialsStorageError,
            CredentialsInvalidError,
        ):
            return False
        except Exception as e:
            logger.debug("unexpected_has_credentials_error", error=str(e), exc_info=e)
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
            accessToken=SecretStr(credentials.access_token),
            refreshToken=SecretStr(credentials.refresh_token),
            expiresAt=int(credentials.expires_at.timestamp() * 1000),
            scopes=["openai-api"],
            subscriptionType="openai",
            tokenType="Bearer",
        )
        return ClaudeCredentials(claudeAiOauth=oauth_token)

    async def is_authenticated(self) -> bool:
        """Check if current authentication is valid with caching.

        Returns:
            True if authenticated, False otherwise
        """
        # Check cache first
        cached_result = self._auth_cache.get_auth_status("openai-codex")
        if cached_result is not None:
            logger.debug("auth_status_cache_hit", authenticated=cached_result)
            return cached_result

        try:
            token = await self.get_valid_token()
            result = bool(token)

            # Cache the result
            self._auth_cache.set_auth_status("openai-codex", result)
            logger.debug("auth_status_cached", authenticated=result)

            return result
        except (AuthenticationError, CredentialsStorageError, CredentialsInvalidError):
            # Cache negative result too (shorter TTL handled by AuthStatusCache)
            self._auth_cache.set_auth_status("openai-codex", False)
            return False
        except Exception as e:
            logger.debug("unexpected_is_authenticated_error", error=str(e), exc_info=e)
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
        """Check if we have valid OpenAI credentials with caching."""
        # Reuse the cached is_authenticated method
        return await self.is_authenticated()

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

    @async_ttl_cache(maxsize=8, ttl=120.0)  # 2 minute cache for expensive auth status
    async def get_auth_status(self) -> dict[str, Any]:
        """Get detailed authentication status information with caching.

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

                except (
                    jwt.DecodeError,
                    jwt.InvalidTokenError,
                    KeyError,
                    ValueError,
                ) as e:
                    logger.debug("token_decode_failed", error=str(e), exc_info=e)
                    # Just add basic info if decoding fails
                    status["expires_at"] = credentials.expires_at.isoformat()
                    status["token_expired"] = credentials.is_expired()
                    status["account_active"] = credentials.active

        except (OSError, PermissionError) as e:
            logger.debug("storage_access_failed", error=str(e), exc_info=e)
            status["auth_error"] = "Storage access failed"
        except (CredentialsStorageError, CredentialsInvalidError) as e:
            logger.debug("credentials_error", error=str(e), exc_info=e)
            status["auth_error"] = "Credentials error"
        except json.JSONDecodeError as e:
            logger.debug("auth_status_json_decode_error", error=str(e), exc_info=e)
            status["auth_error"] = "Token decode failed"
        except ValidationError as e:
            logger.debug("auth_status_validation_error", error=str(e), exc_info=e)
            status["auth_error"] = "Validation error"
        except Exception as e:
            logger.debug("unexpected_auth_status_error", error=str(e), exc_info=e)
            status["auth_error"] = str(e)

        return status

    def invalidate_auth_cache(self) -> None:
        """Clear all cached authentication data."""
        self._auth_cache.clear()
        if hasattr(self.get_auth_status, "cache_clear"):
            self.get_auth_status.cache_clear()
        logger.debug("openai_auth_cache_cleared")

    def invalidate_auth_status(self) -> None:
        """Specifically invalidate auth status for this provider."""
        self._auth_cache.invalidate_auth_status("openai-codex")
        logger.debug("openai_auth_status_invalidated")
