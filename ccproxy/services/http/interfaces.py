"""HTTP service interfaces and protocols."""

from typing import Any, Protocol


class UpstreamResponseExtractor(Protocol):
    """Extracts provider-specific metadata from upstream responses before format conversion."""

    def extract_metadata(self, body: bytes, request_context: Any) -> None:
        """Extract metadata from upstream provider's native response format.

        Args:
            body: Raw response body from upstream provider
            request_context: Context to populate with extracted metadata
        """
