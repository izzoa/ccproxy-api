"""Unified OAuth callback handler for all providers."""

import asyncio
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ccproxy.auth.exceptions import (
    CredentialsStorageError,
    OAuthError,
    OAuthTokenRefreshError,
)
from ccproxy.auth.oauth.base import BaseOAuthClient
from ccproxy.auth.oauth.providers import AnthropicOAuthClient, OpenAIOAuthClient
from ccproxy.auth.oauth.templates import OAuthProvider, OAuthTemplates
from ccproxy.auth.storage.base import TokenStorage
from ccproxy.core.logging import get_logger


logger = get_logger(__name__)


class ProviderType(Enum):
    """OAuth provider types."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"


@dataclass
class OAuthFlow:
    """OAuth flow state information."""

    provider: ProviderType
    code_verifier: str
    state: str
    storage: TokenStorage | None = None
    custom_paths: list[Path] | None = None
    completed: bool = False
    success: bool = False
    error: str | None = None

    def mark_complete(self, success: bool, error: str | None = None) -> None:
        """Mark flow as complete.

        Args:
            success: Whether flow succeeded
            error: Optional error message
        """
        self.completed = True
        self.success = success
        self.error = error


class OAuthCallbackHandler:
    """Consolidated OAuth callback handler for all providers."""

    def __init__(self) -> None:
        """Initialize callback handler."""
        self._pending_flows: dict[str, OAuthFlow] = {}
        self._flow_lock = asyncio.Lock()

    def register_flow(
        self,
        state: str,
        code_verifier: str,
        provider: ProviderType,
        storage: TokenStorage | None = None,
        custom_paths: list[Path] | None = None,
    ) -> None:
        """Register a pending OAuth flow.

        Args:
            state: State parameter for CSRF protection
            code_verifier: PKCE code verifier
            provider: OAuth provider type
            storage: Optional token storage
            custom_paths: Optional custom storage paths
        """
        flow = OAuthFlow(
            provider=provider,
            code_verifier=code_verifier,
            state=state,
            storage=storage,
            custom_paths=custom_paths,
        )
        self._pending_flows[state] = flow

        logger.debug(
            "oauth_flow_registered",
            state=state,
            provider=provider.value,
            has_storage=bool(storage),
            has_custom_paths=bool(custom_paths),
        )

    def get_flow(self, state: str) -> OAuthFlow | None:
        """Get OAuth flow by state.

        Args:
            state: State parameter

        Returns:
            OAuth flow if found, None otherwise
        """
        return self._pending_flows.get(state)

    def remove_flow(self, state: str) -> OAuthFlow | None:
        """Remove and return OAuth flow.

        Args:
            state: State parameter

        Returns:
            OAuth flow if found, None otherwise
        """
        return self._pending_flows.pop(state, None)

    def _get_provider_client(
        self, provider: ProviderType, storage: TokenStorage | None = None
    ) -> BaseOAuthClient:
        """Get OAuth client for provider.

        Args:
            provider: Provider type
            storage: Optional token storage

        Returns:
            OAuth client instance

        Raises:
            ValueError: If provider is unknown
        """
        if provider == ProviderType.ANTHROPIC:
            from ccproxy.auth.storage.json_file import JsonFileTokenStorage

            # Use provided storage or create default
            if storage and isinstance(storage, JsonFileTokenStorage):
                return AnthropicOAuthClient(storage=storage)
            return AnthropicOAuthClient()

        elif provider == ProviderType.OPENAI:
            # OpenAI uses its own storage type, not the generic TokenStorage
            # So we create default if provided storage is incompatible
            from plugins.codex.config import CodexSettings

            return OpenAIOAuthClient(settings=CodexSettings())

        else:
            raise ValueError(f"Unknown provider: {provider}")

    def _get_provider_enum(self, provider: ProviderType) -> OAuthProvider:
        """Convert provider type to template provider enum.

        Args:
            provider: Provider type

        Returns:
            Template provider enum
        """
        if provider == ProviderType.ANTHROPIC:
            return OAuthProvider.CLAUDE
        elif provider == ProviderType.OPENAI:
            return OAuthProvider.OPENAI
        else:
            return OAuthProvider.GENERIC

    async def handle_callback(
        self,
        request: Request,
        code: str | None = None,
        state: str | None = None,
        error: str | None = None,
        error_description: str | None = None,
    ) -> HTMLResponse:
        """Handle OAuth callback for any provider.

        Args:
            request: FastAPI request object
            code: Authorization code from OAuth provider
            state: State parameter for CSRF protection
            error: OAuth error code
            error_description: OAuth error description

        Returns:
            HTML response for the callback
        """
        # Handle OAuth errors
        if error:
            logger.error(
                "oauth_callback_error",
                error=error,
                error_description=error_description,
                state=state,
            )

            # Update flow if state is provided
            if state:
                flow = self.get_flow(state)
                if flow:
                    flow.mark_complete(
                        success=False,
                        error=error_description
                        or error
                        or "OAuth authentication failed",
                    )

            # Determine provider from flow or use generic
            provider_enum = OAuthProvider.GENERIC
            if state:
                flow = self.get_flow(state)
                if flow:
                    provider_enum = self._get_provider_enum(flow.provider)

            return OAuthTemplates.callback_error(
                error=error, error_description=error_description, provider=provider_enum
            )

        # Validate required parameters
        if not code:
            logger.error("oauth_callback_missing_code", state=state)

            if state:
                flow = self.get_flow(state)
                if flow:
                    flow.mark_complete(
                        success=False, error="No authorization code received"
                    )
                    provider_enum = self._get_provider_enum(flow.provider)
                    return OAuthTemplates.missing_code(provider=provider_enum)

            return OAuthTemplates.missing_code()

        if not state:
            logger.error("oauth_callback_missing_state")
            return OAuthTemplates.error(
                error_message="Missing state parameter",
                title="Invalid Request",
                error_detail="The state parameter is required for CSRF protection.",
                status_code=400,
            )

        # Get flow information
        flow = self.get_flow(state)
        if not flow:
            logger.error("oauth_callback_invalid_state", state=state)
            return OAuthTemplates.invalid_state()

        # Get provider information
        provider_enum = self._get_provider_enum(flow.provider)

        try:
            # Get appropriate OAuth client
            client = self._get_provider_client(flow.provider, flow.storage)

            # Exchange code for tokens
            credentials: BaseModel = await client.handle_callback(
                code=code, state=state, code_verifier=flow.code_verifier
            )

            # Save credentials if storage is available
            if flow.storage:
                success = await flow.storage.save(credentials)
                if not success:
                    raise CredentialsStorageError("Failed to save credentials")
            elif flow.custom_paths:
                # Handle custom paths for backward compatibility
                if flow.provider == ProviderType.ANTHROPIC:
                    from ccproxy.auth.storage.json_file import JsonFileTokenStorage

                    storage = JsonFileTokenStorage(flow.custom_paths[0])
                    success = await storage.save(credentials)
                    if not success:
                        raise CredentialsStorageError(
                            "Failed to save credentials to custom path"
                        )

            # Mark flow as successful
            flow.mark_complete(success=True)

            logger.info(
                "oauth_callback_success",
                provider=flow.provider.value,
                state=state,
                has_storage=bool(flow.storage or flow.custom_paths),
            )

            return OAuthTemplates.success(provider=provider_enum)

        except OAuthTokenRefreshError as e:
            logger.error(
                "oauth_token_exchange_failed",
                provider=flow.provider.value,
                error=str(e),
                exc_info=e,
            )
            flow.mark_complete(success=False, error=str(e))
            return OAuthTemplates.token_exchange_failed(
                error_detail=str(e), provider=provider_enum
            )

        except CredentialsStorageError as e:
            logger.error(
                "oauth_storage_failed",
                provider=flow.provider.value,
                error=str(e),
                exc_info=e,
            )
            flow.mark_complete(success=False, error=str(e))
            return OAuthTemplates.storage_error(
                error_detail=str(e), provider=provider_enum
            )

        except OAuthError as e:
            logger.error(
                "oauth_error",
                provider=flow.provider.value,
                error=str(e),
                exc_info=e,
            )
            flow.mark_complete(success=False, error=str(e))
            return OAuthTemplates.error(
                error_message="OAuth authentication failed",
                error_detail=str(e),
                status_code=500,
            )

        except Exception as e:
            logger.error(
                "oauth_unexpected_error",
                provider=flow.provider.value,
                error=str(e),
                error_type=type(e).__name__,
                exc_info=e,
            )
            flow.mark_complete(success=False, error=str(e))
            return OAuthTemplates.error(
                error_message="An unexpected error occurred",
                error_detail=str(e),
                status_code=500,
            )

    async def wait_for_flow(self, state: str, timeout: float = 300.0) -> OAuthFlow:
        """Wait for OAuth flow to complete.

        Args:
            state: State parameter
            timeout: Maximum time to wait in seconds

        Returns:
            Completed OAuth flow

        Raises:
            TimeoutError: If flow doesn't complete within timeout
            OAuthError: If flow fails
        """
        start_time = asyncio.get_event_loop().time()

        while True:
            flow = self.get_flow(state)
            if not flow:
                raise OAuthError("OAuth flow not found")

            if flow.completed:
                if flow.success:
                    return flow
                else:
                    raise OAuthError(flow.error or "OAuth flow failed")

            # Check timeout
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                raise TimeoutError(f"OAuth flow timed out after {timeout} seconds")

            # Wait a bit before checking again
            await asyncio.sleep(0.5)


# Global handler instance
_handler = OAuthCallbackHandler()


def get_oauth_handler() -> OAuthCallbackHandler:
    """Get global OAuth callback handler.

    Returns:
        OAuth callback handler instance
    """
    return _handler
