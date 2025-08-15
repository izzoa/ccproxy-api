"""Authentication service for managing OAuth tokens."""

from datetime import UTC, datetime

import structlog
from fastapi import HTTPException

from ccproxy.models.credentials import CredentialStatus, CredentialValidation
from ccproxy.services.credentials.manager import CredentialsManager


logger = structlog.get_logger(__name__)


class AuthenticationService:
    """Manages authentication token retrieval and validation."""

    def __init__(self, credentials_manager: CredentialsManager) -> None:
        """Initialize with credentials manager.

        - Wraps existing CredentialsManager
        - Provides cleaner interface for ProxyService
        """
        self.credentials_manager = credentials_manager

    async def get_access_token(self) -> str:
        """Retrieve valid OAuth access token.

        - Gets token from credentials manager
        - Validates token is not expired
        - Attempts refresh if needed
        - Raises HTTPException(401) with helpful message
        """
        try:
            # Get current credentials
            credentials = await self.credentials_manager.get_credentials()

            if not credentials or not credentials.access_token:
                raise self._create_auth_error_response(
                    CredentialValidation(
                        status=CredentialStatus.MISSING,
                        message="No credentials found. Please run: ccproxy auth login",
                    )
                )

            # Check if token is expired
            if credentials.expires_at:
                now = datetime.now(UTC)
                expires_at = credentials.expires_at

                # Ensure expires_at is timezone-aware
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=UTC)

                if now >= expires_at:
                    # Try to refresh
                    logger.info("Access token expired, attempting refresh")
                    refreshed = await self.refresh_token_if_needed()

                    if refreshed:
                        # Get new credentials after refresh
                        credentials = await self.credentials_manager.get_credentials()
                        if credentials and credentials.access_token:
                            return credentials.access_token

                    raise self._create_auth_error_response(
                        CredentialValidation(
                            status=CredentialStatus.EXPIRED,
                            message="Token expired and refresh failed",
                            expires_at=expires_at,
                        )
                    )

            return credentials.access_token

        except HTTPException:
            raise
        except Exception as e:
            logger.error("Failed to get access token", error=str(e))
            raise self._create_auth_error_response(
                CredentialValidation(
                    status=CredentialStatus.INVALID,
                    message=f"Authentication error: {str(e)}",
                )
            )

    async def validate_credentials(self) -> CredentialValidation:
        """Check credential status without retrieving token.

        - Returns validation object with status
        - Includes expiry information
        - Indicates if refresh is needed
        """
        return await self.credentials_manager.validate_credentials()

    async def refresh_token_if_needed(self) -> bool:
        """Attempt to refresh token if expired.

        - Checks expiry status first
        - Calls refresh on credentials manager
        - Returns True if successful
        - Returns False if refresh fails
        """
        try:
            validation = await self.validate_credentials()

            if validation.status == CredentialStatus.EXPIRED:
                logger.info("Attempting token refresh")
                refreshed = await self.credentials_manager.refresh_token()

                if refreshed:
                    logger.info("Token refresh successful")
                    return True
                else:
                    logger.warning("Token refresh failed")
                    return False

            # Token not expired, no refresh needed
            return True

        except Exception as e:
            logger.error("Error during token refresh", error=str(e))
            return False

    def _create_auth_error_response(
        self, validation: CredentialValidation | None
    ) -> HTTPException:
        """Build detailed 401 error with helpful message.

        - Includes 'ccproxy auth login' instructions
        - Shows expiry time if token expired
        - Provides different messages for different failures
        """
        if not validation:
            message = "Authentication required. Please run: ccproxy auth login"
        elif validation.status == CredentialStatus.MISSING:
            message = (
                validation.message
                or "No credentials found. Please run: ccproxy auth login"
            )
        elif validation.status == CredentialStatus.EXPIRED:
            if validation.expires_at:
                message = f"Access token expired at {validation.expires_at}. Please run: ccproxy auth login"
            else:
                message = "Access token expired. Please run: ccproxy auth login"
        elif validation.status == CredentialStatus.INVALID:
            message = (
                validation.message
                or "Invalid credentials. Please run: ccproxy auth login"
            )
        else:
            message = "Authentication failed. Please run: ccproxy auth login"

        return HTTPException(
            status_code=401, detail=message, headers={"WWW-Authenticate": "Bearer"}
        )
