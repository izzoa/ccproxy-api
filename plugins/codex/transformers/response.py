"""Codex response transformer - passthrough pattern."""

import structlog

logger = structlog.get_logger(__name__)


class CodexResponseTransformer:
    """Transform responses from Codex API.
    
    Handles:
    - Header filtering and CORS addition
    - Body passthrough (no transformation)
    """
    
    def __init__(self) -> None:
        """Initialize the response transformer."""
        pass
    
    def transform_headers(self, headers: dict[str, str]) -> dict[str, str]:
        """Transform response headers.
        
        Args:
            headers: Original response headers
            
        Returns:
            Filtered headers with CORS
        """
        transformed = {}
        
        # Headers to exclude
        excluded = {
            "content-length",
            "transfer-encoding",
            "content-encoding",
            "connection",
        }
        
        # Pass through non-excluded headers
        for key, value in headers.items():
            if key.lower() not in excluded:
                transformed[key] = value
        
        # Add CORS headers
        transformed["Access-Control-Allow-Origin"] = "*"
        transformed["Access-Control-Allow-Headers"] = "*"
        transformed["Access-Control-Allow-Methods"] = "*"
        
        return transformed
    
    def transform_body(self, body: bytes | None) -> bytes | None:
        """Transform response body - passthrough.
        
        Args:
            body: Original response body
            
        Returns:
            Response body unchanged
        """
        return body