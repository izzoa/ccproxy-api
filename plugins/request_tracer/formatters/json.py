"""JSON formatter for structured request/response logging."""

import json
import logging
from pathlib import Path
from typing import Any, Optional

import structlog


logger = structlog.get_logger(__name__)

# Import TRACE_LEVEL
try:
    from ccproxy.core.logging import TRACE_LEVEL
except ImportError:
    TRACE_LEVEL = 5  # Fallback


class JSONFormatter:
    """Formats requests/responses as structured JSON for observability."""

    def __init__(self, config: Any) -> None:
        """Initialize with configuration.

        Args:
            config: RequestTracerConfig instance
        """
        self.config = config
        self.verbose_api = config.verbose_api
        self.json_logs_enabled = config.json_logs_enabled
        self.redact_sensitive = config.redact_sensitive
        self.truncate_body_preview = config.truncate_body_preview

        # Check if TRACE level is enabled
        current_level = (
            logger._context.get("_level", logging.INFO)
            if hasattr(logger, "_context")
            else logging.INFO
        )
        self.trace_enabled = self.verbose_api or current_level <= TRACE_LEVEL

        # Setup log directory if file logging is enabled
        self.request_log_dir = None
        if self.json_logs_enabled:
            self.request_log_dir = Path(config.get_json_log_dir())
            self.request_log_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def redact_headers(headers: dict[str, str]) -> dict[str, str]:
        """Redact sensitive headers for safe logging.

        - Replaces authorization, x-api-key, cookie values with [REDACTED]
        - Preserves header names for debugging
        - Returns new dict without modifying original
        """
        sensitive_headers = {
            "authorization",
            "x-api-key",
            "api-key",
            "cookie",
            "x-auth-token",
            "x-access-token",
            "x-secret-key",
        }

        redacted = {}
        for key, value in headers.items():
            if key.lower() in sensitive_headers:
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = value
        return redacted

    async def log_request(
        self,
        request_id: str,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
    ) -> None:
        """Log structured request data.

        - Logs at TRACE level with redacted headers
        - Writes to request log file with complete data (if configured)
        """
        if not self.trace_enabled:
            return

        # Log at TRACE level with redacted headers
        log_headers = self.redact_headers(headers) if self.redact_sensitive else headers

        if hasattr(logger, "trace"):
            logger.trace(
                "api_request",
                category="http",
                request_id=request_id,
                method=method,
                url=url,
                headers=log_headers,
                body_size=len(body) if body else 0,
            )
        elif self.verbose_api:
            # Fallback for backward compatibility
            logger.info(
                "api_request",
                category="http",
                request_id=request_id,
                method=method,
                url=url,
                headers=log_headers,
                body_size=len(body) if body else 0,
            )

        # Write to file if configured
        if self.request_log_dir and self.json_logs_enabled:
            request_file = self.request_log_dir / f"{request_id}_request.json"
            request_data = {
                "request_id": request_id,
                "method": method,
                "url": url,
                "headers": dict(headers),  # Full headers in file
                "body": body.decode("utf-8", errors="replace") if body else None,
            }
            request_file.write_text(json.dumps(request_data, indent=2))

    async def log_response(
        self,
        request_id: str,
        status: int,
        headers: dict[str, str],
        body: bytes,
    ) -> None:
        """Log structured response data.

        - Logs at TRACE level
        - Truncates body preview for console
        - Handles binary data gracefully
        """
        if not self.trace_enabled:
            return

        body_preview = self._get_body_preview(body)

        # Log at TRACE level
        if hasattr(logger, "trace"):
            logger.trace(
                "api_response",
                category="http",
                request_id=request_id,
                status=status,
                headers=dict(headers),
                body_preview=body_preview,
                body_size=len(body),
            )
        elif self.verbose_api:
            # Fallback for backward compatibility
            logger.info(
                "api_response",
                category="http",
                request_id=request_id,
                status=status,
                headers=dict(headers),
                body_preview=body_preview,
                body_size=len(body),
            )

        # Write to file if configured
        if self.request_log_dir and self.json_logs_enabled:
            response_file = self.request_log_dir / f"{request_id}_response.json"
            response_data = {
                "request_id": request_id,
                "status": status,
                "headers": dict(headers),
                "body": body.decode("utf-8", errors="replace"),
            }
            response_file.write_text(json.dumps(response_data, indent=2))

    def _get_body_preview(self, body: bytes) -> str:
        """Extract readable preview from body bytes.

        - Decodes UTF-8 with error replacement
        - Truncates to max_length
        - Returns '<binary data>' for non-text content
        """
        max_length = self.truncate_body_preview

        try:
            text = body.decode("utf-8", errors="replace")

            # Try to parse as JSON for better formatting
            try:
                json_data = json.loads(text)
                formatted = json.dumps(json_data, indent=2)
                if len(formatted) > max_length:
                    return formatted[:max_length] + "..."
                return formatted
            except json.JSONDecodeError:
                # Not JSON, return as plain text
                if len(text) > max_length:
                    return text[:max_length] + "..."
                return text
        except UnicodeDecodeError:
            return "<binary data>"
        except Exception as e:
            logger.debug("text_formatting_unexpected_error", error=str(e))
            return "<binary data>"

    # Streaming methods
    async def log_stream_start(
        self, request_id: str, headers: dict[str, str]
    ) -> None:
        """Mark beginning of stream with initial headers."""
        if not self.verbose_api:
            return

        logger.info(
            "stream_start",
            category="streaming",
            request_id=request_id,
            headers=dict(headers)
        )

    async def log_stream_chunk(
        self, request_id: str, chunk: bytes, chunk_number: int
    ) -> None:
        """Record individual stream chunk (optional, for deep debugging)."""
        if not self.config.log_streaming_chunks:
            return

        logger.debug(
            "stream_chunk",
            category="streaming",
            request_id=request_id,
            chunk_number=chunk_number,
            chunk_size=len(chunk),
        )

    async def log_stream_complete(
        self, request_id: str, total_chunks: int, total_bytes: int
    ) -> None:
        """Mark stream completion with statistics."""
        if not self.verbose_api:
            return

        logger.info(
            "stream_complete",
            category="streaming",
            request_id=request_id,
            total_chunks=total_chunks,
            total_bytes=total_bytes,
        )
