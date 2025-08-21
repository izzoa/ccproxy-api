"""Claude token storage implementation (simplified)."""

from pathlib import Path

from pydantic import ValidationError

from ccproxy.auth.exceptions import CredentialsInvalidError
from ccproxy.auth.models import ClaudeCredentials
from ccproxy.auth.storage.base import BaseJsonStorage
from ccproxy.core.logging import get_logger


logger = get_logger(__name__)


class ClaudeTokenStorage(BaseJsonStorage[ClaudeCredentials]):
    """Storage for Claude credentials (simplified from JsonFileTokenStorage).

    This class extends BaseJsonStorage to provide Claude-specific
    credential handling while reusing common JSON operations.
    """

    def __init__(self, file_path: Path):
        """Initialize Claude token storage.

        Args:
            file_path: Path to the JSON credentials file
        """
        super().__init__(file_path)

    async def load(self) -> ClaudeCredentials | None:
        """Load Claude credentials from JSON file.

        Returns:
            Parsed credentials if found and valid, None otherwise

        Raises:
            CredentialsInvalidError: If the JSON file is invalid
            CredentialsStorageError: If there's an error reading the file
        """
        # Get logger with request context at the start of the function
        logger = get_logger(__name__)

        try:
            logger.debug(
                "credentials_load_start",
                source="claude_file",
                path=str(self.file_path),
            )

            # Use base class method to read JSON
            data = await self._read_json()
            if not data:
                logger.debug("credentials_file_empty", path=str(self.file_path))
                return None

            # Parse into Claude credentials
            credentials = ClaudeCredentials.model_validate(data)

            logger.debug(
                "credentials_load_completed",
                source="claude_file",
                has_oauth=bool(credentials.claude_ai_oauth),
            )

            return credentials

        except ValidationError as e:
            logger.error(
                "credentials_validation_error",
                path=str(self.file_path),
                error=str(e),
                exc_info=e,
            )
            raise CredentialsInvalidError(
                f"Invalid credentials format in {self.file_path}: {e}"
            ) from e

    async def save(self, credentials: ClaudeCredentials) -> bool:
        """Save Claude credentials to JSON file.

        Args:
            credentials: Credentials to save

        Returns:
            True if saved successfully, False otherwise

        Raises:
            CredentialsStorageError: If there's an error writing the file
        """
        try:
            # Convert to dict with proper aliases
            data = credentials.model_dump(by_alias=True, mode="json")

            # Use base class method to write JSON
            await self._write_json(data)

            logger.debug(
                "credentials_save_completed",
                source="claude_file",
                path=str(self.file_path),
            )
            return True

        except ValidationError as e:
            logger.error(
                "credentials_validation_error",
                path=str(self.file_path),
                error=str(e),
                exc_info=e,
            )
            raise CredentialsInvalidError(f"Invalid credentials format: {e}") from e
