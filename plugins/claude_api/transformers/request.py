"""Claude API request transformer."""

import json
from typing import Any

from ccproxy.core.logging import get_logger


logger = get_logger(__name__)


class ClaudeAPIRequestTransformer:
    """Transform requests for Claude API.

    Handles:
    - Header transformation and injection of detected Claude CLI headers
    - System prompt injection from detected Claude CLI data
    - OAuth token conversion (Bearer -> x-api-key)
    """

    def __init__(self, detection_service: Any | None = None):
        """Initialize the request transformer.

        Args:
            detection_service: ClaudeAPIDetectionService instance for header/prompt injection
        """
        self.detection_service = detection_service

    def transform_headers(
        self,
        headers: dict[str, str],
        session_id: str = "",
        access_token: str | None = None,
        **kwargs: Any,
    ) -> dict[str, str]:
        """Transform request headers.

        Injects detected Claude CLI headers for proper authentication.

        Args:
            headers: Original request headers
            session_id: Optional session ID
            access_token: Optional access token
            **kwargs: Additional parameters

        Returns:
            Transformed headers with Claude CLI headers injected
        """
        # Get logger with request context at the start of the function
        logger = get_logger(__name__)

        # Debug logging
        logger.debug(
            "transform_headers_called",
            has_access_token=access_token is not None,
            access_token_length=len(access_token) if access_token else 0,
            header_count=len(headers),
            has_x_api_key="x-api-key" in headers,
            has_authorization="Authorization" in headers,
            category="transform",
        )

        transformed = headers.copy()

        # Remove hop-by-hop headers and x-api-key (client auth header)
        hop_by_hop = {
            "host",
            "connection",
            "keep-alive",
            "transfer-encoding",
            "content-length",
            "upgrade",
            "proxy-authenticate",
            "proxy-authorization",
            "te",
            "trailer",
            "x-api-key",  # Remove client's x-api-key header
        }
        transformed = {
            k: v for k, v in transformed.items() if k.lower() not in hop_by_hop
        }

        # Inject detected headers if available
        has_detected_headers = False
        if self.detection_service:
            cached_data = self.detection_service.get_cached_data()
            if cached_data and cached_data.headers:
                detected_headers = cached_data.headers.to_headers_dict()
                logger.debug(
                    "injecting_detected_headers",
                    version=cached_data.claude_version,
                    header_count=len(detected_headers),
                    category="transform",
                )
                # Detected headers take precedence
                transformed.update(detected_headers)
                has_detected_headers = True

        # Only convert to x-api-key if we don't have detected headers
        # (detected headers should include proper Authorization)
        if not has_detected_headers:
            # Convert Authorization header to x-api-key if needed
            # First check if we got an access_token parameter (from new interface)
            if access_token:  # and "x-api-key" not in transformed:
                transformed["x-api-key"] = access_token
                # Remove any Authorization header since we're using x-api-key
                transformed.pop("Authorization", None)
                logger.debug("converted_bearer_to_api_key", category="transform")
            # Fallback to old method if no access_token parameter
            elif "Authorization" in transformed and "x-api-key" not in transformed:
                auth_header = transformed.pop("Authorization")
                if auth_header.startswith("Bearer "):
                    token = auth_header.replace("Bearer ", "")
                    transformed["x-api-key"] = token
                    logger.debug("converted_bearer_to_api_key", category="transform")
        else:
            logger.debug("using_detected_headers_for_auth", category="transform")

        # Debug logging - what headers are we returning?
        logger.debug(
            "transform_headers_result",
            has_x_api_key="x-api-key" in transformed,
            has_authorization="Authorization" in transformed,
            header_count=len(transformed),
            detected_headers_used=has_detected_headers,
            category="transform",
        )

        return transformed

    def transform_body(self, body: bytes | None) -> bytes | None:
        """Transform request body.

        Injects detected system prompt from Claude CLI.

        Args:
            body: Original request body

        Returns:
            Transformed body with system prompt injected
        """
        # Get logger with request context at the start of the function
        logger = get_logger(__name__)

        logger.debug(
            "transform_body_called",
            has_body=body is not None,
            body_length=len(body) if body else 0,
            has_detection_service=self.detection_service is not None,
            category="transform",
        )

        if not body:
            return body

        try:
            data = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning(
                "body_decode_failed",
                error=str(e),
                body_preview=body[:100].decode("utf-8", errors="replace")
                if body
                else None,
                category="transform",
            )
            return body

        # Inject system prompt if available
        if self.detection_service:
            cached_data = self.detection_service.get_cached_data()
            logger.debug(
                "checking_cached_data",
                has_cached_data=cached_data is not None,
                has_system_prompt=cached_data.system_prompt is not None
                if cached_data
                else False,
                has_system_field=cached_data.system_prompt.system_field is not None
                if cached_data and cached_data.system_prompt
                else False,
                system_already_in_data="system" in data,
                category="transform",
            )
            if cached_data and cached_data.system_prompt and "system" not in data:
                # Inject the detected system prompt (as list or string)
                data["system"] = cached_data.system_prompt.system_field
                logger.debug(
                    "injected_system_prompt",
                    version=cached_data.claude_version,
                    system_type=type(cached_data.system_prompt.system_field).__name__,
                    system_length=len(str(cached_data.system_prompt.system_field)),
                    category="transform",
                )
            else:
                logger.debug(
                    "system_prompt_not_injected",
                    reason="no_cached_data"
                    if not cached_data
                    else "no_system_prompt"
                    if not cached_data.system_prompt
                    else "system_already_exists"
                    if "system" in data
                    else "unknown",
                    category="transform",
                )
        else:
            logger.debug("no_detection_service_available", category="transform")

        return json.dumps(data).encode("utf-8")

    async def transform(
        self, headers: dict[str, str], body: bytes | None
    ) -> tuple[dict[str, str], bytes | None]:
        """Transform both headers and body.

        Args:
            headers: Request headers
            body: Request body

        Returns:
            Tuple of (transformed_headers, transformed_body)
        """
        transformed_headers = self.transform_headers(headers)
        transformed_body = self.transform_body(body)
        return transformed_headers, transformed_body

    async def adapt_request(self, request_json: dict[str, Any]) -> dict[str, Any]:
        """Adapt request for compatibility with ProxyService.

        This method provides the interface expected by ProxyService.

        Args:
            request_json: Request body as JSON dict

        Returns:
            Transformed request body as JSON dict
        """
        # Convert to bytes for transformation
        body_bytes = json.dumps(request_json).encode("utf-8")

        # Apply body transformation (system prompt injection)
        transformed_bytes = self.transform_body(body_bytes)

        # Convert back to dict
        if transformed_bytes:
            result = json.loads(transformed_bytes.decode("utf-8"))
            if isinstance(result, dict):
                return result
            # If not a dict, return original
            return request_json
        return request_json
