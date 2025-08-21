"""OpenAI OAuth provider implementation."""

from typing import TYPE_CHECKING, Any

import jwt

from ccproxy.auth.exceptions import OAuthError
from ccproxy.auth.models import OpenAICredentials
from ccproxy.auth.oauth.base import BaseOAuthClient
from ccproxy.core.logging import get_logger


if TYPE_CHECKING:
    from ccproxy.auth.storage.openai import OpenAITokenStorage
    from plugins.codex.config import CodexSettings

logger = get_logger(__name__)


class OpenAIOAuthClient(BaseOAuthClient):
    """OpenAI OAuth implementation using Codex format."""

    def __init__(
        self,
        settings: "CodexSettings | None" = None,
        storage: "OpenAITokenStorage | None" = None,
    ):
        """Initialize OpenAI OAuth client.

        Args:
            settings: Codex configuration settings
            storage: Token storage backend
        """
        # Import here to avoid circular import
        from plugins.codex.config import CodexSettings

        # Use settings or create default
        self.settings = settings or CodexSettings()

        # Initialize base class
        super().__init__(
            client_id=self.settings.oauth.client_id,
            redirect_uri=self.settings.get_redirect_uri(),
            base_url=self.settings.oauth.base_url,
            scopes=self.settings.oauth.scopes,
            storage=storage or OpenAITokenStorage(),
        )

    def _get_auth_endpoint(self) -> str:
        """Get OpenAI OAuth authorization endpoint.

        Returns:
            Full authorization endpoint URL
        """
        return f"{self.base_url}/oauth/authorize"

    def _get_token_endpoint(self) -> str:
        """Get OpenAI OAuth token exchange endpoint.

        Returns:
            Full token endpoint URL
        """
        return f"{self.base_url}/oauth/token"

    def get_custom_headers(self) -> dict[str, str]:
        """Get OpenAI-specific HTTP headers.

        Returns:
            Dictionary of custom headers
        """
        return {
            "User-Agent": f"ccproxy-openai-oauth/{self.settings.oauth.client_id}",
        }

    async def parse_token_response(self, data: dict[str, Any]) -> OpenAICredentials:
        """Parse OpenAI-specific token response.

        Args:
            data: Raw token response from OpenAI

        Returns:
            OpenAI credentials object

        Raises:
            OAuthError: If response parsing fails
        """
        try:
            # Calculate expiration time
            expires_in = data.get("expires_in", 3600)  # Default 1 hour
            expires_at = self._calculate_expiration(expires_in)

            # Capture id_token if available (contains chatgpt_account_id for proper UUID)
            id_token = data.get("id_token")
            if not id_token:
                logger.debug("No id_token in OpenAI OAuth response")

            # Create credentials (account_id will be extracted from tokens by validator)
            credentials = OpenAICredentials(
                access_token=data["access_token"],
                refresh_token=data.get("refresh_token", ""),
                id_token=id_token,
                expires_at=expires_at,
                account_id="",  # Will be auto-extracted by validator
                active=True,
            )

            logger.info(
                "openai_credentials_parsed",
                has_id_token=bool(id_token),
                has_refresh_token=bool(data.get("refresh_token")),
                expires_in=expires_in,
            )

            return credentials

        except KeyError as e:
            logger.error(
                "openai_token_response_missing_field",
                missing_field=str(e),
                response_keys=list(data.keys()),
            )
            raise OAuthError(f"Missing required field in token response: {e}") from e
        except Exception as e:
            logger.error(
                "openai_token_response_parse_error",
                error=str(e),
                error_type=type(e).__name__,
            )
            raise OAuthError(f"Failed to parse OpenAI token response: {e}") from e

    def _extract_account_id_from_token(self, token: str) -> str | None:
        """Extract account ID from JWT token.

        Args:
            token: JWT token (access_token or id_token)

        Returns:
            Account ID if found, None otherwise
        """
        try:
            # Decode JWT without verification to extract claims
            decoded = jwt.decode(token, options={"verify_signature": False})

            # Look for OpenAI auth claims with chatgpt_account_id (proper UUID)
            if "https://api.openai.com/auth" in decoded:
                auth_claims = decoded["https://api.openai.com/auth"]
                if isinstance(auth_claims, dict):
                    # Use chatgpt_account_id if available (this is the proper UUID)
                    if "chatgpt_account_id" in auth_claims:
                        account_id = auth_claims["chatgpt_account_id"]
                        logger.debug(
                            "extracted_chatgpt_account_id", account_id=account_id
                        )
                        return str(account_id)

                    # Also check organization_id as a fallback
                    if "organization_id" in auth_claims:
                        org_id = auth_claims["organization_id"]
                        if not org_id.startswith("auth0|"):
                            logger.debug("extracted_organization_id", org_id=org_id)
                            return str(org_id)

            # Check top-level claims
            if "account_id" in decoded:
                return str(decoded["account_id"])
            elif "org_id" in decoded:
                org_id = decoded["org_id"]
                # Check if org_id looks like a UUID (not auth0|xxx format)
                if not org_id.startswith("auth0|"):
                    return str(org_id)
            elif "sub" in decoded:
                # Fallback to auth0 sub (not ideal but maintains compatibility)
                sub = decoded["sub"]
                logger.warning(
                    "falling_back_to_auth0_sub",
                    sub=sub[:20] + "..." if len(sub) > 20 else sub,
                )
                return str(sub)

        except (jwt.DecodeError, jwt.InvalidTokenError, KeyError, ValueError) as e:
            logger.debug("jwt_decode_failed", error=str(e))

        return None

    async def refresh_token(self, refresh_token: str) -> OpenAICredentials:
        """Refresh OpenAI access token.

        Args:
            refresh_token: Refresh token

        Returns:
            New OpenAI credentials

        Raises:
            OAuthError: If refresh fails
        """
        token_endpoint = self._get_token_endpoint()
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
        }
        headers = self.get_custom_headers()
        headers["Content-Type"] = "application/x-www-form-urlencoded"

        try:
            import httpx

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    token_endpoint,
                    data=data,
                    headers=headers,
                    timeout=30.0,
                )
                response.raise_for_status()

                token_response = response.json()
                return await self.parse_token_response(token_response)

        except Exception as e:
            logger.error("openai_token_refresh_failed", error=str(e), exc_info=e)
            raise OAuthError(f"Failed to refresh OpenAI token: {e}") from e
