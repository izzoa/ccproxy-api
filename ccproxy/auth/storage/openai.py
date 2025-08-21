"""OpenAI token storage implementation."""

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import jwt

from ccproxy.auth.exceptions import CredentialsInvalidError, CredentialsStorageError
from ccproxy.auth.storage.base import BaseJsonStorage
from ccproxy.core.logging import get_logger


if TYPE_CHECKING:
    from ccproxy.auth.models import OpenAICredentials


logger = get_logger(__name__)


class OpenAITokenStorage(BaseJsonStorage["OpenAICredentials"]):
    """Storage for OpenAI credentials using Codex format.

    This class extends BaseJsonStorage to provide OpenAI-specific
    credential handling while reusing common JSON operations.
    """

    def __init__(self, file_path: Path | None = None):
        """Initialize OpenAI token storage.

        Args:
            file_path: Path to JSON file. If None, uses ~/.codex/auth.json
        """
        file_path = file_path or Path.home() / ".codex" / "auth.json"
        super().__init__(file_path)

    async def load(self) -> "OpenAICredentials | None":
        """Load OpenAI credentials from Codex JSON file.

        Returns:
            Parsed credentials if found and valid, None otherwise

        Raises:
            CredentialsInvalidError: If the JSON file is invalid
            CredentialsStorageError: If there's an error reading the file
        """
        try:
            # Use base class method to read JSON
            data = await self._read_json()
            if not data:
                return None

            # Extract tokens section
            tokens = data.get("tokens", {})
            if not tokens:
                logger.warning("no_tokens_section", path=str(self.file_path))
                return None

            # Get required fields
            access_token = tokens.get("access_token")
            if not access_token:
                logger.warning("no_access_token", path=str(self.file_path))
                return None

            # Extract expiration from JWT token
            expires_at = self._extract_expiration_from_token(access_token)
            if not expires_at:
                logger.warning("no_expiration", path=str(self.file_path))
                return None

            # Import here to avoid circular import
            from ccproxy.auth.models import OpenAICredentials

            # Create credentials object
            credentials_data = {
                "access_token": access_token,
                "refresh_token": tokens.get("refresh_token", ""),
                "id_token": tokens.get("id_token"),
                "expires_at": expires_at,
                "account_id": tokens.get("account_id", ""),
                "active": True,
            }

            credentials = OpenAICredentials.from_dict(credentials_data)

            logger.debug(
                "openai_credentials_loaded",
                path=str(self.file_path),
                has_refresh_token=bool(tokens.get("refresh_token")),
            )

            return credentials

        except Exception as e:
            if isinstance(e, CredentialsInvalidError | CredentialsStorageError):
                raise
            logger.error(
                "unexpected_load_error",
                path=str(self.file_path),
                error=str(e),
                exc_info=e,
            )
            return None

    async def save(self, credentials: "OpenAICredentials") -> bool:
        """Save OpenAI credentials to Codex JSON file.

        Args:
            credentials: Credentials to save

        Returns:
            True if saved successfully, False otherwise

        Raises:
            CredentialsStorageError: If there's an error writing the file
        """
        try:
            # Load existing file to preserve OPENAI_API_KEY if present
            existing_data = await self._read_json()

            # Prepare Codex JSON data structure
            codex_data = {
                "OPENAI_API_KEY": existing_data.get("OPENAI_API_KEY"),
                "tokens": {
                    "id_token": credentials.id_token,
                    "access_token": credentials.access_token,
                    "refresh_token": credentials.refresh_token,
                    "account_id": credentials.account_id,
                },
                "last_refresh": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }

            # Use base class method to write JSON
            await self._write_json(codex_data)

            logger.info(
                "openai_credentials_saved",
                path=str(self.file_path),
                account_id_prefix=credentials.account_id[:8]
                if credentials.account_id
                else "none",
            )
            return True

        except Exception as e:
            if isinstance(e, CredentialsStorageError):
                raise
            logger.error(
                "unexpected_save_error",
                path=str(self.file_path),
                error=str(e),
                exc_info=e,
            )
            return False

    async def exists(self) -> bool:
        """Check if OpenAI credentials exist and are valid.

        Returns:
            True if valid credentials exist, False otherwise
        """
        # First check if file exists
        if not await super().exists():
            return False

        try:
            # Check if file contains valid tokens
            data = await self._read_json()
            tokens = data.get("tokens", {})
            return bool(tokens.get("access_token"))
        except Exception:
            return False

    def _extract_expiration_from_token(self, access_token: str) -> datetime | None:
        """Extract expiration time from JWT access token.

        Args:
            access_token: JWT access token

        Returns:
            Expiration datetime or None if cannot extract
        """
        try:
            decoded = jwt.decode(access_token, options={"verify_signature": False})
            exp_timestamp = decoded.get("exp")
            if exp_timestamp:
                return datetime.fromtimestamp(exp_timestamp, tz=UTC)
        except (jwt.DecodeError, jwt.InvalidTokenError, KeyError, ValueError) as e:
            logger.debug("jwt_decode_failed", error=str(e))
        return None
