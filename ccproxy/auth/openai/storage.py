"""JSON file storage for OpenAI credentials using Codex format."""

import asyncio
import contextlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import jwt
import structlog
from pydantic import ValidationError

from ccproxy.auth.exceptions import CredentialsInvalidError, CredentialsStorageError


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
        if not await asyncio.to_thread(self.file_path.exists):
            return None

        try:
            # Run file I/O in thread pool to avoid blocking
            def read_file() -> dict[str, Any]:
                with self.file_path.open("r") as f:
                    return json.load(f)  # type: ignore[no-any-return]

            data = await asyncio.to_thread(read_file)

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

        except json.JSONDecodeError as e:
            logger.error(
                "json_parse_failed",
                file_path=str(self.file_path),
                error=str(e),
                line=e.lineno,
                exc_info=e,
            )
            raise CredentialsInvalidError("Invalid JSON format in auth file") from e
        except UnicodeDecodeError as e:
            logger.error(
                "unicode_decode_failed",
                file_path=str(self.file_path),
                error=str(e),
                exc_info=e,
            )
            raise CredentialsInvalidError("Invalid file encoding") from e
        except (OSError, PermissionError) as e:
            logger.error(
                "file_access_failed",
                file_path=str(self.file_path),
                error=str(e),
                exc_info=e,
            )
            raise CredentialsStorageError("Failed to access auth file") from e
        except ValidationError as e:
            logger.error(
                "credentials_validation_error",
                file_path=str(self.file_path),
                error=str(e),
                exc_info=e,
            )
            raise CredentialsInvalidError("Invalid credentials format") from e
        except Exception as e:
            logger.error(
                "credentials_load_unexpected_error",
                file_path=str(self.file_path),
                error=str(e),
                exc_info=e,
            )
            return None

    def _extract_expiration_from_token(self, access_token: str) -> datetime | None:
        """Extract expiration time from JWT access token."""
        try:
            decoded = jwt.decode(access_token, options={"verify_signature": False})
            exp_timestamp = decoded.get("exp")
            if exp_timestamp:
                return datetime.fromtimestamp(exp_timestamp, tz=UTC)
        except (jwt.DecodeError, jwt.InvalidTokenError) as e:
            logger.warning("jwt_decode_failed", error=str(e), exc_info=e)
        except KeyError as e:
            logger.warning("jwt_missing_exp_claim", error=str(e), exc_info=e)
        except ValueError as e:
            logger.warning("jwt_timestamp_parse_failed", error=str(e), exc_info=e)
        except Exception as e:
            logger.warning("jwt_decode_unexpected_error", error=str(e), exc_info=e)
        return None

    async def save(self, credentials: "OpenAICredentials") -> bool:
        """Save credentials to Codex JSON file."""
        try:
            # Create directory if it doesn't exist - run in thread pool
            await asyncio.to_thread(
                self.file_path.parent.mkdir, parents=True, exist_ok=True
            )

            # Load existing file or create new structure
            existing_data: dict[str, Any] = {}
            if await asyncio.to_thread(self.file_path.exists):
                try:
                    # Run file reading in thread pool
                    def read_existing() -> dict[str, Any]:
                        with self.file_path.open("r") as f:
                            return json.load(f)  # type: ignore[no-any-return]

                    existing_data = await asyncio.to_thread(read_existing)
                except json.JSONDecodeError as e:
                    logger.warning(
                        "existing_file_parse_failed", error=str(e), exc_info=e
                    )
                except (OSError, PermissionError) as e:
                    logger.warning(
                        "existing_file_access_failed", error=str(e), exc_info=e
                    )

            # Prepare Codex JSON data structure
            codex_data = {
                "OPENAI_API_KEY": existing_data.get("OPENAI_API_KEY"),
                "tokens": {
                    "id_token": credentials.id_token
                    or existing_data.get("tokens", {}).get("id_token"),
                    "access_token": credentials.access_token,
                    "refresh_token": credentials.refresh_token,
                    "account_id": credentials.account_id,
                },
                "last_refresh": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }

            # Write atomically by writing to temp file then renaming - run in thread pool
            temp_file = self.file_path.with_suffix(f"{self.file_path.suffix}.tmp")

            def write_file() -> None:
                with temp_file.open("w") as f:
                    json.dump(codex_data, f, indent=2)

                # Set restrictive permissions (readable only by owner)
                temp_file.chmod(0o600)

                # Atomic rename
                temp_file.replace(self.file_path)

            await asyncio.to_thread(write_file)

            logger.info(
                "Saved OpenAI credentials to Codex auth file",
                file_path=str(self.file_path),
            )
            return True

        except (OSError, PermissionError) as e:
            logger.error(
                "file_write_failed",
                file_path=str(self.file_path),
                error=str(e),
                exc_info=e,
            )
            # Clean up temp file if it exists
            temp_file = self.file_path.with_suffix(f"{self.file_path.suffix}.tmp")
            if temp_file.exists():
                with contextlib.suppress(Exception):
                    temp_file.unlink()
            raise CredentialsStorageError("Failed to write auth file") from e
        except UnicodeEncodeError as e:
            logger.error(
                "unicode_encode_failed",
                file_path=str(self.file_path),
                error=str(e),
                exc_info=e,
            )
            # Clean up temp file if it exists
            temp_file = self.file_path.with_suffix(f"{self.file_path.suffix}.tmp")
            if temp_file.exists():
                with contextlib.suppress(Exception):
                    temp_file.unlink()
            raise CredentialsStorageError("Failed to encode credentials") from e
        except (TypeError, ValueError) as e:
            logger.error(
                "json_encode_failed",
                file_path=str(self.file_path),
                error=str(e),
                exc_info=e,
            )
            # Clean up temp file if it exists
            temp_file = self.file_path.with_suffix(f"{self.file_path.suffix}.tmp")
            if temp_file.exists():
                with contextlib.suppress(Exception):
                    temp_file.unlink()
            raise CredentialsStorageError("Failed to encode credentials as JSON") from e
        except ValidationError as e:
            logger.error(
                "credentials_validation_error",
                file_path=str(self.file_path),
                error=str(e),
                exc_info=e,
            )
            # Clean up temp file if it exists
            temp_file = self.file_path.with_suffix(f"{self.file_path.suffix}.tmp")
            if temp_file.exists():
                with contextlib.suppress(Exception):
                    temp_file.unlink()
            raise CredentialsInvalidError("Invalid credentials data") from e
        except Exception as e:
            logger.error(
                "credentials_save_unexpected_error",
                file_path=str(self.file_path),
                error=str(e),
                exc_info=e,
            )
            # Clean up temp file if it exists
            temp_file = self.file_path.with_suffix(f"{self.file_path.suffix}.tmp")
            if temp_file.exists():
                with contextlib.suppress(Exception):
                    temp_file.unlink()
            return False

    async def exists(self) -> bool:
        """Check if credentials file exists."""
        if not await asyncio.to_thread(self.file_path.exists):
            return False

        try:
            # Run file reading in thread pool
            def read_and_check() -> bool:
                with self.file_path.open("r") as f:
                    data = json.load(f)
                tokens = data.get("tokens", {})
                return bool(tokens.get("access_token"))

            return await asyncio.to_thread(read_and_check)
        except json.JSONDecodeError:
            return False
        except (OSError, PermissionError):
            return False
        except Exception as e:
            logger.debug(
                "unexpected_exists_error",
                file_path=str(self.file_path),
                error=str(e),
                exc_info=e,
            )
            return False

    async def delete(self) -> bool:
        """Delete credentials file."""
        try:
            if await asyncio.to_thread(self.file_path.exists):
                await asyncio.to_thread(self.file_path.unlink)
                logger.info("Deleted Codex auth file", file_path=str(self.file_path))
            return True
        except (OSError, PermissionError) as e:
            logger.error(
                "file_delete_failed",
                file_path=str(self.file_path),
                error=str(e),
                exc_info=e,
            )
            raise CredentialsStorageError("Failed to delete auth file") from e
        except Exception as e:
            logger.error(
                "delete_unexpected_error",
                file_path=str(self.file_path),
                error=str(e),
                exc_info=e,
            )
            return False

    def get_location(self) -> str:
        """Get storage location description."""
        return str(self.file_path)
