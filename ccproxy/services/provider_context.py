"""Provider context configuration for unified request handling."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ccproxy.adapters.base import APIAdapter
from ccproxy.auth.base import AuthManager


@runtime_checkable
class PluginTransformerProtocol(Protocol):
    """Protocol for plugin-based transformers with header and body methods."""

    def transform_headers(
        self, headers: dict[str, str], *args: Any, **kwargs: Any
    ) -> dict[str, str]:
        """Transform request headers."""
        ...

    def transform_body(self, body: Any) -> Any:
        """Transform request body."""
        ...


@dataclass
class ProviderContext:
    """Configuration context for a provider.

    This class encapsulates all provider-specific configuration needed
    to handle requests in a unified manner across different AI providers.
    """

    # Required fields
    provider_name: str
    auth_manager: AuthManager
    target_base_url: str

    # Optional adapters for format conversion
    request_adapter: APIAdapter | None = None
    response_adapter: APIAdapter | None = None

    # Optional request transformer (for headers, etc.)
    request_transformer: PluginTransformerProtocol | None = None

    # Optional response transformer (for headers, etc.)
    response_transformer: PluginTransformerProtocol | None = None

    # Optional path transformer (for path mapping after prefix stripping)
    path_transformer: Callable[[str], str] | None = None

    # Optional route prefix to strip from request paths (e.g., "/api/codex")
    route_prefix: str | None = None

    # Provider-specific settings
    session_id: str | None = None
    account_id: str | None = None
    timeout: float = 240.0

    # Feature flags
    supports_streaming: bool = True
    requires_session: bool = False

    # Additional headers to inject
    extra_headers: dict[str, str] = field(default_factory=dict)
