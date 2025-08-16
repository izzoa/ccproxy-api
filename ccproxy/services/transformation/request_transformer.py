"""Request transformation service for body, headers, and URL manipulation."""

import json
from collections.abc import Callable
from urllib.parse import urljoin

import structlog

from ccproxy.adapters.base import APIAdapter
from ccproxy.services.provider_context import ProviderContext


logger = structlog.get_logger(__name__)


class RequestTransformer:
    """Handles all request transformation operations."""

    # Headers that shouldn't be forwarded to upstream
    HOP_BY_HOP_HEADERS = {
        "host",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "content-length",
        "content-encoding",
    }

    def extract_request_metadata(self, body: bytes | None) -> tuple[str | None, bool]:
        """Extract model name and streaming flag from request.

        - Parses JSON body safely
        - Extracts 'model' field
        - Extracts 'stream' field (default False)
        - Returns (model, is_streaming)
        """
        if not body:
            return None, False

        try:
            data = json.loads(body)
            model = data.get("model")
            is_streaming = data.get("stream", False) is True
            return model, is_streaming
        except (json.JSONDecodeError, TypeError):
            return None, False

    async def transform_body(
        self,
        body: bytes,
        adapter: APIAdapter | None,
        provider_context: ProviderContext,
    ) -> bytes:
        """Apply adapter and provider transformations to body.

        - First applies adapter.adapt_request if available
        - Then applies provider transformer if present
        - Handles JSON encode/decode cycle
        - Falls back to original on errors
        """
        if not body:
            return body

        try:
            # Parse request body
            request_data = json.loads(body)

            # Apply adapter transformation first (format conversion)
            if adapter:
                request_data = await adapter.adapt_request(request_data)

            # Apply provider-specific transformation (pass original bytes)
            if provider_context.request_transformer and hasattr(
                provider_context.request_transformer, "transform_body"
            ):
                logger.info(
                    "calling_transform_body", provider=provider_context.provider_name
                )
                # Convert back to bytes for transformer that expects bytes
                body_bytes = json.dumps(request_data).encode() if adapter else body
                transformed_bytes = provider_context.request_transformer.transform_body(
                    body_bytes
                )
                if transformed_bytes is not None:
                    # Parse the transformed bytes back to dict
                    request_data = json.loads(transformed_bytes.decode())
                logger.info(
                    "transform_body_completed",
                    body_length=len(json.dumps(request_data)) if request_data else 0,
                )

            # Return transformed body
            return json.dumps(request_data).encode()

        except Exception as e:
            logger.warning(
                "Failed to transform request body",
                error=str(e),
                provider=provider_context.provider_name,
            )
            return body

    async def prepare_headers(
        self,
        request_headers: dict[str, str],
        auth_headers: dict[str, str],
        extra_headers: dict[str, str],
        provider_context: ProviderContext,
    ) -> dict[str, str]:
        """Merge and transform headers for outbound request.

        - Removes hop-by-hop headers first
        - Applies provider request_transformer
        - Merges auth headers appropriately
        - Extra headers take precedence
        """
        # Start with cleaned request headers
        headers = self._remove_hop_by_hop_headers(request_headers)

        # Apply provider-specific header transformation
        if provider_context.request_transformer:
            # Extract access token from auth headers if available
            access_token = None
            if "Authorization" in auth_headers:
                access_token = auth_headers["Authorization"].replace("Bearer ", "")

            # Check if transformer has transform_headers method
            if hasattr(provider_context.request_transformer, "transform_headers"):
                # Call with proper arguments for compatibility
                session_id = getattr(provider_context, "session_id", "")
                transformed = provider_context.request_transformer.transform_headers(
                    headers,
                    session_id=session_id,
                    access_token=access_token,
                    provider_name=provider_context.provider_name,
                )
                if transformed:
                    headers = transformed

        # Merge auth headers (these should override request headers)
        headers.update(auth_headers)

        # Apply any extra headers (highest priority)
        headers.update(extra_headers)

        # Ensure content-type is set for JSON APIs
        if "content-type" not in headers:
            headers["content-type"] = "application/json"

        return headers

    def build_target_url(
        self,
        base_url: str,
        path: str,
        query: str | None,
        provider_context: ProviderContext,
    ) -> str:
        """Construct target URL with transformations.

        - Strips route prefix if configured
        - Applies path transformer function
        - Handles plugin protocols (preserves non-http/https schemes)
        - Appends query string if present
        """
        # Strip route prefix if configured
        if provider_context.route_prefix:
            path = self._strip_route_prefix(path, provider_context.route_prefix)

        # Apply path transformer if provided
        if provider_context.path_transformer:
            path = self._apply_path_transformer(path, provider_context.path_transformer)

        # Build full URL - handle plugin protocols specially
        from urllib.parse import urlparse

        parsed_base = urlparse(base_url)
        if parsed_base.scheme not in ("http", "https", ""):
            # This is a plugin protocol - manually construct URL to preserve scheme
            # Ensure path starts with / for proper joining
            if not path.startswith("/"):
                path = "/" + path
            url = f"{base_url.rstrip('/')}{path}"
        else:
            # Standard HTTP/HTTPS - use urljoin
            url = urljoin(base_url, path)

        # Append query string if present
        if query:
            url = f"{url}?{query}"

        return url

    def _strip_route_prefix(self, path: str, prefix: str | None) -> str:
        """Remove route prefix from path if present.

        - Only strips if path starts with prefix
        - Ensures result starts with '/'
        - Handles empty prefix gracefully
        """
        if not prefix:
            return path

        if path.startswith(prefix):
            path = path[len(prefix) :]
            if not path.startswith("/"):
                path = "/" + path

        return path

    def _apply_path_transformer(
        self, path: str, transformer: Callable[[str], str] | None
    ) -> str:
        """Apply provider-specific path transformation.

        - Calls transformer function if provided
        - Returns original path if no transformer
        - Handles transformer exceptions
        """
        if not transformer:
            return path

        try:
            transformed = transformer(path)
            return transformed if transformed else path
        except Exception as e:
            logger.warning("Path transformer failed", error=str(e), original_path=path)
            return path

    @staticmethod
    def _remove_hop_by_hop_headers(headers: dict[str, str]) -> dict[str, str]:
        """Remove headers that shouldn't be forwarded.

        - Removes: host, connection, keep-alive
        - Removes: transfer-encoding, content-length
        - Handles case variations (lower, title, upper)
        - Returns new dict without modifications
        """
        cleaned = {}
        for key, value in headers.items():
            if key.lower() not in RequestTransformer.HOP_BY_HOP_HEADERS:
                cleaned[key] = value
        return cleaned
