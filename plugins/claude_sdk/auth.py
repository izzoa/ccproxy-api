"""No-op auth manager for Claude SDK plugin."""

from typing import Any


class NoOpAuthManager:
    """No-operation auth manager for Claude SDK.
    
    The SDK handles authentication internally through the CLI,
    so we don't need to manage auth headers.
    """

    async def get_auth_headers(self) -> dict[str, str]:
        """Return empty auth headers since SDK handles auth internally."""
        return {}

    async def validate_credentials(self) -> bool:
        """Always return True since SDK handles auth internally."""
        return True

    def has_credentials(self) -> bool:
        """Always return True since SDK handles auth internally."""
        return True

    async def refresh_token(self) -> bool:
        """No-op - SDK handles auth internally."""
        return True

    async def get_credentials(self) -> dict[str, Any]:
        """Return empty credentials since SDK handles auth internally."""
        return {}