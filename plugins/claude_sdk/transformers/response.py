"""Response transformer for Claude SDK plugin.

This module handles Claude SDK-specific response transformations,
including headers and body modifications.
"""

import structlog


logger = structlog.get_logger(__name__)


class ClaudeSDKResponseTransformer:
    """Transform responses from Claude SDK operations.

    This transformer handles SDK-specific response modifications,
    primarily for headers since body transformation is handled
    by format adapters.
    """

    def __init__(self) -> None:
        """Initialize the response transformer."""
        self.logger = logger

    def transform_headers(self, headers: dict[str, str]) -> dict[str, str]:
        """Transform response headers from Claude SDK.

        Add SDK-specific headers to indicate the response came from SDK.

        Args:
            headers: Original response headers

        Returns:
            Transformed headers
        """
        transformed = headers.copy()

        # Add SDK indicator headers
        transformed["X-Claude-SDK-Response"] = "true"

        # Ensure proper content type for streaming
        if "text/event-stream" in transformed.get("content-type", ""):
            # Already set correctly for SSE
            pass
        elif "application/json" not in transformed.get("content-type", ""):
            # Default to JSON if not set
            transformed["content-type"] = "application/json"

        self.logger.debug(
            "claude_sdk_response_headers_transformed",
            original_count=len(headers),
            transformed_count=len(transformed),
        )

        return transformed

    def transform_body(self, body: bytes | None) -> bytes | None:
        """Transform response body from Claude SDK.

        Body transformation is handled by format adapters and the
        streaming processor, so this is usually a passthrough.

        Args:
            body: Original response body

        Returns:
            Transformed body (usually unchanged)
        """
        # Body transformation is handled by format adapters
        # and the handler's response conversion logic
        return body
