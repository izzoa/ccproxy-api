"""Base authentication manager interface for all providers."""

from abc import ABC, abstractmethod


class AuthManager(ABC):
    """Base authentication manager for all providers."""

    @abstractmethod
    async def get_auth_headers(self) -> dict[str, str]:
        """Get authentication headers for the request."""
        pass

    @abstractmethod
    async def validate_credentials(self) -> bool:
        """Validate that credentials are available and valid."""
        pass

    @abstractmethod
    def get_provider_name(self) -> str:
        """Get the provider name for logging."""
        pass
