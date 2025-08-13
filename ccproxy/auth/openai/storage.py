"""JSON file storage for OpenAI credentials using Codex format."""

import contextlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import jwt
import structlog


if TYPE_CHECKING:
    from .credentials import OpenAICredentials


logger = structlog.get_logger(__name__)


class OpenAITokenStorage:
    """JSON file-based storage for OpenAI credentials using Codex format."""

    def __init__(self, file_path: Path | None = None):
        """Initialize storage with file path.

        Args:
            file_path: Path to JSON file. If None, uses ~/.codex/auth.json
        """
        self.file_path = file_path or Path.home() / ".codex" / "auth.json"

    async def load(self) -> "OpenAICredentials | None":
        """Load credentials from Codex JSON file."""
        if not self.file_path.exists():
            return None

        try:
            with self.file_path.open("r") as f:
                data = json.load(f)

            # Extract tokens section
            tokens = data.get("tokens", {})
            if not tokens:
                logger.warning("No tokens section found in Codex auth file")
                return None

            # Get required fields
            access_token = tokens.get("access_token")
            refresh_token = tokens.get("refresh_token")
            account_id = tokens.get("account_id")

            if not access_token:
                logger.warning("No access_token found in Codex auth file")
                return None

            # Extract expiration from JWT token
            expires_at = self._extract_expiration_from_token(access_token)
            if not expires_at:
                logger.warning("Could not extract expiration from access token")
                return None

            # Import here to avoid circular import
            from .credentials import OpenAICredentials

            # Create credentials object
            credentials_data = {
                "access_token": access_token,
                "refresh_token": refresh_token or "",
                "expires_at": expires_at,
                "account_id": account_id or "",
                "active": True,
            }

            return OpenAICredentials.from_dict(credentials_data)

        except Exception as e:
            logger.error(
                "Failed to load OpenAI credentials from Codex auth file",
                file_path=str(self.file_path),
                error=str(e),
            )
            return None

    def _extract_expiration_from_token(self, access_token: str) -> datetime | None:
        """Extract expiration time from JWT access token."""
        try:
            decoded = jwt.decode(access_token, options={"verify_signature": False})
            exp_timestamp = decoded.get("exp")
            if exp_timestamp:
                return datetime.fromtimestamp(exp_timestamp, tz=UTC)
        except Exception as e:
            logger.warning("Failed to decode JWT token for expiration", error=str(e))
        return None

    async def save(self, credentials: "OpenAICredentials") -> bool:
        """Save credentials to Codex JSON file."""
        try:
            # Create directory if it doesn't exist
            self.file_path.parent.mkdir(parents=True, exist_ok=True)

            # Load existing file or create new structure
            existing_data: dict[str, Any] = {}
            if self.file_path.exists():
                try:
                    with self.file_path.open("r") as f:
                        existing_data = json.load(f)
                except Exception:
                    logger.warning(
                        "Could not load existing auth file, creating new one"
                    )

            # Prepare Codex JSON data structure
            codex_data = {
                "OPENAI_API_KEY": existing_data.get("OPENAI_API_KEY"),
                "tokens": {
                    "id_token": existing_data.get("tokens", {}).get("id_token"),
                    "access_token": credentials.access_token,
                    "refresh_token": credentials.refresh_token,
                    "account_id": credentials.account_id,
                },
                "last_refresh": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }

            # Write atomically by writing to temp file then renaming
            temp_file = self.file_path.with_suffix(f"{self.file_path.suffix}.tmp")

            with temp_file.open("w") as f:
                json.dump(codex_data, f, indent=2)

            # Set restrictive permissions (readable only by owner)
            temp_file.chmod(0o600)

            # Atomic rename
            temp_file.replace(self.file_path)

            logger.info(
                "Saved OpenAI credentials to Codex auth file",
                file_path=str(self.file_path),
            )
            return True

        except Exception as e:
            logger.error(
                "Failed to save OpenAI credentials to Codex auth file",
                file_path=str(self.file_path),
                error=str(e),
            )
            # Clean up temp file if it exists
            temp_file = self.file_path.with_suffix(f"{self.file_path.suffix}.tmp")
            if temp_file.exists():
                with contextlib.suppress(Exception):
                    temp_file.unlink()
            return False

    async def exists(self) -> bool:
        """Check if credentials file exists."""
        if not self.file_path.exists():
            return False

        try:
            with self.file_path.open("r") as f:
                data = json.load(f)
            tokens = data.get("tokens", {})
            return bool(tokens.get("access_token"))
        except Exception:
            return False

    async def delete(self) -> bool:
        """Delete credentials file."""
        try:
            if self.file_path.exists():
                self.file_path.unlink()
                logger.info("Deleted Codex auth file", file_path=str(self.file_path))
            return True
        except Exception as e:
            logger.error(
                "Failed to delete Codex auth file",
                file_path=str(self.file_path),
                error=str(e),
            )
            return False

    def get_location(self) -> str:
        """Get storage location description."""
        return str(self.file_path)
