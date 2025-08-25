"""
Observability event classes for the CCProxy API.

These events represent different stages of request processing and are used by the
ObservabilityPipeline to coordinate logging, metrics collection, and monitoring.

Event Types:
- ClientRequestEvent: Incoming client request received
- ClientResponseEvent: Response sent back to client
- ProviderRequestEvent: Outgoing request to AI provider
- ProviderResponseEvent: Response received from AI provider
"""

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional


if TYPE_CHECKING:
    from ccproxy.observability.context import RequestContext


@dataclass
class ClientRequestEvent:
    """Event emitted when a client request is received."""

    request_id: str
    method: str
    path: str
    query: str | None = None
    headers: dict[str, str] | None = None
    body: bytes | None = None
    client_ip: str | None = None
    user_agent: str | None = None
    timestamp: float | None = None
    context: Optional["RequestContext"] = None

    def __post_init__(self) -> None:
        """Set timestamp if not provided."""
        if self.timestamp is None:
            self.timestamp = time.time()


@dataclass
class ClientResponseEvent:
    """Event emitted when a response is sent to a client."""

    request_id: str
    status_code: int
    headers: dict[str, str] | None = None
    body: bytes | None = None
    body_size: int = 0
    duration_ms: float = 0
    error: str | None = None
    timestamp: float | None = None
    context: Optional["RequestContext"] = None

    def __post_init__(self) -> None:
        """Set timestamp if not provided."""
        if self.timestamp is None:
            self.timestamp = time.time()


@dataclass
class ProviderRequestEvent:
    """Event emitted when a request is sent to an AI provider."""

    request_id: str
    provider: str
    method: str
    url: str
    headers: dict[str, str] | None = None
    body: bytes | None = None
    timestamp: float | None = None
    context: Optional["RequestContext"] = None

    def __post_init__(self) -> None:
        """Set timestamp if not provided."""
        if self.timestamp is None:
            self.timestamp = time.time()


@dataclass
class ProviderResponseEvent:
    """Event emitted when a response is received from an AI provider."""

    request_id: str
    provider: str
    status_code: int
    headers: dict[str, str] | None = None
    body: bytes | None = None
    duration_ms: float = 0
    tokens_input: int | None = None
    tokens_output: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    cost_usd: float | None = None
    model: str | None = None
    timestamp: float | None = None
    context: Optional["RequestContext"] = None

    def __post_init__(self) -> None:
        """Set timestamp if not provided."""
        if self.timestamp is None:
            self.timestamp = time.time()
