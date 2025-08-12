"""Codex-specific transformers for request/response transformation."""

import json

import structlog
from typing_extensions import TypedDict

from ccproxy.core.transformers import RequestTransformer
from ccproxy.core.types import ProxyRequest, TransformContext
from ccproxy.models.detection import CodexCacheData


logger = structlog.get_logger(__name__)


class CodexRequestData(TypedDict):
    """Typed structure for transformed Codex request data."""

    method: str
    url: str
    headers: dict[str, str]
    body: bytes | None


class CodexRequestTransformer(RequestTransformer):
    """Codex request transformer for header and instructions field injection."""

    def __init__(self) -> None:
        """Initialize Codex request transformer."""
        super().__init__()

    async def _transform_request(
        self, request: ProxyRequest, context: TransformContext | None = None
    ) -> ProxyRequest:
        """Transform a proxy request for Codex API.

        Args:
            request: The structured proxy request to transform
            context: Optional transformation context

        Returns:
            The transformed proxy request
        """
        # Extract required data from context
        access_token = ""
        session_id = ""
        account_id = ""
        codex_detection_data = None

        if context:
            if hasattr(context, "access_token"):
                access_token = context.access_token
            elif isinstance(context, dict):
                access_token = context.get("access_token", "")

            if hasattr(context, "session_id"):
                session_id = context.session_id
            elif isinstance(context, dict):
                session_id = context.get("session_id", "")

            if hasattr(context, "account_id"):
                account_id = context.account_id
            elif isinstance(context, dict):
                account_id = context.get("account_id", "")

            if hasattr(context, "codex_detection_data"):
                codex_detection_data = context.codex_detection_data
            elif isinstance(context, dict):
                codex_detection_data = context.get("codex_detection_data")

        # Transform URL - remove codex prefix and forward to ChatGPT backend
        transformed_url = self._transform_codex_url(request.url)

        # Convert request body to bytes for header processing
        body_bytes = None
        if request.body:
            if isinstance(request.body, bytes):
                body_bytes = request.body
            elif isinstance(request.body, str):
                body_bytes = request.body.encode("utf-8")
            elif isinstance(request.body, dict):
                body_bytes = json.dumps(request.body).encode("utf-8")

        # Transform headers with Codex CLI identity
        transformed_headers = self.create_codex_headers(
            request.headers,
            access_token,
            session_id,
            account_id,
            body_bytes,
            codex_detection_data,
        )

        # Transform body to inject instructions
        transformed_body = request.body
        if request.body:
            if isinstance(request.body, bytes):
                transformed_body = self.transform_codex_body(
                    request.body, codex_detection_data
                )
            else:
                # Convert to bytes if needed
                body_bytes = (
                    json.dumps(request.body).encode("utf-8")
                    if isinstance(request.body, dict)
                    else str(request.body).encode("utf-8")
                )
                transformed_body = self.transform_codex_body(
                    body_bytes, codex_detection_data
                )

        # Create new transformed request
        return ProxyRequest(
            method=request.method,
            url=transformed_url,
            headers=transformed_headers,
            params={},  # Query params handled in URL
            body=transformed_body,
            protocol=request.protocol,
            timeout=request.timeout,
            metadata=request.metadata,
        )

    async def transform_codex_request(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes | None,
        access_token: str,
        session_id: str,
        account_id: str,
        codex_detection_data: CodexCacheData | None = None,
        target_base_url: str = "https://chatgpt.com/backend-api/codex",
    ) -> CodexRequestData:
        """Transform Codex request using direct parameters from ProxyService.

        Args:
            method: HTTP method
            path: Request path
            headers: Request headers
            body: Request body
            access_token: OAuth access token
            session_id: Codex session ID
            account_id: ChatGPT account ID
            codex_detection_data: Optional Codex detection data
            target_base_url: Base URL for the Codex API

        Returns:
            Dictionary with transformed request data (method, url, headers, body)
        """
        # Transform URL path
        transformed_path = self._transform_codex_path(path)
        target_url = f"{target_base_url.rstrip('/')}{transformed_path}"

        # Transform body first (inject instructions)
        codex_body = None
        if body:
            # body is guaranteed to be bytes due to parameter type
            codex_body = self.transform_codex_body(body, codex_detection_data)

        # Transform headers with Codex CLI identity and authentication
        codex_headers = self.create_codex_headers(
            headers, access_token, session_id, account_id, body, codex_detection_data
        )

        # Update Content-Length if body was transformed and size changed
        if codex_body and body and len(codex_body) != len(body):
            # Remove any existing content-length headers (case-insensitive)
            codex_headers = {
                k: v for k, v in codex_headers.items() if k.lower() != "content-length"
            }
            codex_headers["Content-Length"] = str(len(codex_body))
        elif codex_body and not body:
            # New body was created where none existed
            codex_headers["Content-Length"] = str(len(codex_body))

        return CodexRequestData(
            method=method,
            url=target_url,
            headers=codex_headers,
            body=codex_body,
        )

    def _transform_codex_url(self, url: str) -> str:
        """Transform URL from proxy format to ChatGPT backend format."""
        # Extract base URL and path
        if "://" in url:
            protocol, rest = url.split("://", 1)
            if "/" in rest:
                domain, path = rest.split("/", 1)
                path = "/" + path
            else:
                path = "/"
        else:
            path = url if url.startswith("/") else "/" + url

        # Transform path and build target URL
        transformed_path = self._transform_codex_path(path)
        return f"https://chatgpt.com/backend-api/codex{transformed_path}"

    def _transform_codex_path(self, path: str) -> str:
        """Transform request path for Codex API."""
        # Remove /codex prefix if present
        if path.startswith("/codex"):
            path = path[6:]  # Remove "/codex" prefix

        # Ensure we have a valid path
        if not path or path == "/":
            path = "/responses"

        # Handle session_id in path for /codex/{session_id}/responses pattern
        if path.startswith("/") and "/" in path[1:]:
            # This might be /{session_id}/responses - extract the responses part
            parts = path.strip("/").split("/")
            if len(parts) >= 2 and parts[-1] == "responses":
                # Keep the /responses endpoint, session_id will be in headers
                path = "/responses"

        return path

    def create_codex_headers(
        self,
        headers: dict[str, str],
        access_token: str,
        session_id: str,
        account_id: str,
        body: bytes | None = None,
        codex_detection_data: CodexCacheData | None = None,
    ) -> dict[str, str]:
        """Create Codex headers with CLI identity and authentication."""
        codex_headers = {}

        # Strip potentially problematic headers
        excluded_headers = {
            "host",
            "x-forwarded-for",
            "x-forwarded-proto",
            "x-forwarded-host",
            "forwarded",
            # Authentication headers to be replaced
            "authorization",
            "x-api-key",
            # Compression headers to avoid decompression issues
            "accept-encoding",
            "content-encoding",
            # CORS headers - should not be forwarded to upstream
            "origin",
            "access-control-request-method",
            "access-control-request-headers",
            "access-control-allow-origin",
            "access-control-allow-methods",
            "access-control-allow-headers",
            "access-control-allow-credentials",
            "access-control-max-age",
            "access-control-expose-headers",
        }

        # Copy important headers (excluding problematic ones)
        for key, value in headers.items():
            lower_key = key.lower()
            if lower_key not in excluded_headers:
                codex_headers[key] = value

        # Set authentication with OAuth token
        if access_token:
            codex_headers["Authorization"] = f"Bearer {access_token}"

        # Set defaults for essential headers
        if "content-type" not in [k.lower() for k in codex_headers]:
            codex_headers["Content-Type"] = "application/json"
        if "accept" not in [k.lower() for k in codex_headers]:
            codex_headers["Accept"] = "application/json"

        # Use detected Codex CLI headers when available
        if codex_detection_data:
            detected_headers = codex_detection_data.headers.to_headers_dict()
            # Override with session-specific values
            detected_headers["session_id"] = session_id
            if account_id:
                detected_headers["chatgpt-account-id"] = account_id
            codex_headers.update(detected_headers)
            logger.debug(
                "using_detected_codex_headers",
                version=codex_detection_data.codex_version,
            )
        else:
            # Fallback to hardcoded Codex headers
            codex_headers.update(
                {
                    "session_id": session_id,
                    "originator": "codex_cli_rs",
                    "openai-beta": "responses=experimental",
                    "version": "0.21.0",
                }
            )
            if account_id:
                codex_headers["chatgpt-account-id"] = account_id
            logger.debug("using_fallback_codex_headers")

        # Set Accept header based on user's original streaming request
        user_requested_streaming = self._is_user_streaming_request(body)
        if user_requested_streaming:
            codex_headers["Accept"] = "text/event-stream"
            logger.debug("setting_streaming_accept_header", user_requested=True)
        # Note: Don't set Accept to application/json when stream:true is in body
        # as this causes 400 Bad Request from ChatGPT. Instead, we'll handle
        # response conversion in the proxy service.

        return codex_headers

    def _is_streaming_request(self, body: bytes | None) -> bool:
        """Check if the request body indicates a streaming request (including injected default)."""
        if not body:
            return False

        try:
            data = json.loads(body.decode("utf-8"))
            return data.get("stream", False) is True
        except (json.JSONDecodeError, UnicodeDecodeError):
            return False

    def _is_user_streaming_request(self, body: bytes | None) -> bool:
        """Check if the user explicitly requested streaming (has 'stream' field in original body)."""
        if not body:
            return False

        try:
            data = json.loads(body.decode("utf-8"))
            # Only return True if user explicitly included "stream" field (regardless of its value)
            return "stream" in data and data.get("stream") is True
        except (json.JSONDecodeError, UnicodeDecodeError):
            return False

    def transform_codex_body(
        self, body: bytes, codex_detection_data: CodexCacheData | None = None
    ) -> bytes:
        """Transform request body to inject Codex CLI instructions."""
        if not body:
            return body

        try:
            data = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            # Return original if not valid JSON
            logger.warning(
                "codex_transform_json_decode_failed",
                error=str(e),
                body_preview=body[:200].decode("utf-8", errors="replace")
                if body
                else None,
                body_length=len(body) if body else 0,
            )
            return body

        # Get the instructions to inject
        detected_instructions = None
        if codex_detection_data:
            detected_instructions = codex_detection_data.instructions.instructions_field
        else:
            # Fallback instructions from req.json
            detected_instructions = (
                "You are a coding agent running in the Codex CLI, a terminal-based coding assistant. "
                "Codex CLI is an open source project led by OpenAI. You are expected to be precise, safe, and helpful.\n\n"
                "Your capabilities:\n"
                "- Receive user prompts and other context provided by the harness, such as files in the workspace.\n"
                "- Communicate with the user by streaming thinking & responses, and by making & updating plans.\n"
                "- Emit function calls to run terminal commands and apply patches. Depending on how this specific run is configured, "
                "you can request that these function calls be escalated to the user for approval before running. "
                'More on this in the "Sandbox and approvals" section.\n\n'
                "Within this context, Codex refers to the open-source agentic coding interface "
                "(not the old Codex language model built by OpenAI)."
            )

        # Always inject/override the instructions field
        data["instructions"] = detected_instructions

        # Only inject stream: true if user explicitly requested streaming or didn't specify
        # For now, we'll inject stream: true by default since Codex seems to expect it
        if "stream" not in data:
            data["stream"] = True

        return json.dumps(data, separators=(",", ":")).encode("utf-8")
