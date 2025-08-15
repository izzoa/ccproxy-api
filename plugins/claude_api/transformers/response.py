"""Claude API response transformer."""

from typing import Any

import structlog


logger = structlog.get_logger(__name__)


class ClaudeAPIResponseTransformer:
    """Transform responses from Claude API.

    Handles:
    - Header passthrough and filtering
    - Error response preservation
    - Server header forwarding
    """

    def __init__(self):
        """Initialize the response transformer."""
        pass

    def transform_headers(self, headers: dict[str, str]) -> dict[str, str]:
        """Transform response headers.

        Preserves important headers from upstream including server headers.

        Args:
            headers: Original response headers from upstream

        Returns:
            Transformed headers preserving server identification
        """
        transformed = {}

        # Headers to exclude from passthrough
        excluded = {
            "content-length",  # Will be recalculated
            "transfer-encoding",  # Handled by framework
            "content-encoding",  # Decompression handled by httpx
            "connection",  # Hop-by-hop header
            "date",
        }

        # Pass through most headers, including server headers
        for key, value in headers.items():
            if key.lower() not in excluded:
                transformed[key] = value

        # Add CORS headers for browser compatibility
        transformed["Access-Control-Allow-Origin"] = "*"
        transformed["Access-Control-Allow-Headers"] = "*"
        transformed["Access-Control-Allow-Methods"] = "*"

        return transformed

    def transform_body(self, body: bytes | None) -> bytes | None:
        """Transform response body.

        For Claude API, we pass through the body as-is to preserve
        the exact response format, especially for error responses.

        Args:
            body: Original response body

        Returns:
            Response body (unchanged for Claude API)
        """
        # Pass through body unchanged - no transformation needed
        # This ensures error responses are preserved exactly as returned by Claude API
        return body

    async def transform(
        self, status_code: int, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, dict[str, str], bytes | None]:
        """Transform response components.

        Args:
            status_code: HTTP status code
            headers: Response headers
            body: Response body

        Returns:
            Tuple of (status_code, transformed_headers, transformed_body)
        """
        transformed_headers = self.transform_headers(headers)
        transformed_body = self.transform_body(body)

        # Preserve the original status code (including error codes)
        return status_code, transformed_headers, transformed_body

    async def adapt_response(self, response_json: dict[str, Any]) -> dict[str, Any]:
        """Adapt response for compatibility with ProxyService.

        This method provides the interface expected by ProxyService.
        For Claude API, we pass through responses unchanged.

        Args:
            response_json: Response body as JSON dict

        Returns:
            Response body as JSON dict (unchanged)
        """
        # Pass through response unchanged
        return response_json

