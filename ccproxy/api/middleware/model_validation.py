"""Model validation middleware for request validation."""

import json
from typing import Any, Literal

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from ccproxy.core.logging import get_logger
from ccproxy.models.provider import ModelCard
from ccproxy.utils.model_registry import get_model_registry
from ccproxy.utils.token_counting import count_messages_tokens


logger = get_logger(__name__)


class ModelValidationError(Exception):
    """Exception raised when model validation fails."""

    def __init__(self, message: str, error_type: str = "invalid_request_error"):
        """Initialize validation error.

        Args:
            message: Error message
            error_type: Error type for OpenAI-compatible response
        """
        self.message = message
        self.error_type = error_type
        super().__init__(message)


class ModelValidationMiddleware(BaseHTTPMiddleware):
    """Middleware to validate requests against model metadata."""

    def __init__(
        self,
        app: Any,
        validate_token_limits: bool = True,
        enforce_capabilities: bool = True,
        warn_on_limits: bool = True,
        warn_threshold: float = 0.9,
    ):
        """Initialize validation middleware.

        Args:
            app: FastAPI application
            validate_token_limits: Whether to enforce token limits
            enforce_capabilities: Whether to enforce model capabilities
            warn_on_limits: Whether to add warning headers
            warn_threshold: Token usage % to trigger warnings (0.0-1.0)
        """
        super().__init__(app)
        self.validate_token_limits = validate_token_limits
        self.enforce_capabilities = enforce_capabilities
        self.warn_on_limits = warn_on_limits
        self.warn_threshold = warn_threshold
        self.registry = get_model_registry()

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Process request with model validation.

        Args:
            request: Incoming request
            call_next: Next middleware/handler

        Returns:
            Response from next handler or validation error
        """
        if not self._should_validate(request):
            return await call_next(request)

        try:
            body = await request.body()
            if not body:
                return await call_next(request)

            async def receive():
                return {"type": "http.request", "body": body, "more_body": False}

            request._receive = receive

            try:
                request_data = json.loads(body)
            except json.JSONDecodeError:
                return await call_next(request)

            validation_result = await self._validate_request(request, request_data)

            if validation_result.get("error"):
                return self._create_error_response(validation_result["error"])

            response = await call_next(request)

            if validation_result.get("warnings"):
                for warning in validation_result["warnings"]:
                    response.headers.append("X-Model-Warning", warning)

            return response

        except Exception as e:
            logger.error("validation_middleware_error", error=str(e), exc_info=e)
            return await call_next(request)

    def _should_validate(self, request: Request) -> bool:
        """Check if request should be validated.

        Args:
            request: Incoming request

        Returns:
            True if validation should be performed
        """
        path = request.url.path

        validate_paths = [
            "/v1/chat/completions",
            "/v1/messages",
            "/v1/responses",
            "/messages",
            "/chat/completions",
            "/responses",
        ]

        return any(path.endswith(vpath) for vpath in validate_paths)

    async def _validate_request(
        self, request: Request, request_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Validate request against model metadata.

        Args:
            request: Incoming request
            request_data: Parsed request body

        Returns:
            Dict with 'error' (if validation failed) and 'warnings' (if any)
        """
        result: dict[str, Any] = {"warnings": []}

        model_id = request_data.get("model")
        if not model_id:
            return result

        provider = self._infer_provider(request.url.path)
        model_metadata = await self.registry.get_model(model_id, provider=provider)

        if not model_metadata:
            logger.debug(
                "model_metadata_not_found",
                model_id=model_id,
                provider=provider,
            )
            return result

        if hasattr(request.state, "context"):
            request.state.context.model_metadata = model_metadata

        input_tokens: int | None = None
        if self.validate_token_limits or self.warn_on_limits:
            messages = request_data.get("messages", [])
            system = request_data.get("system")
            try:
                input_tokens = count_messages_tokens(
                    messages, model_metadata.id, system
                )
            except Exception as e:
                logger.warning("token_counting_failed", error=str(e))

        if self.validate_token_limits and input_tokens is not None:
            token_error = self._validate_token_limits(
                request_data, model_metadata, input_tokens
            )
            if token_error:
                result["error"] = token_error
                return result

        if self.warn_on_limits and input_tokens is not None:
            warnings = self._check_token_warnings(
                request_data, model_metadata, input_tokens
            )
            result["warnings"].extend(warnings)

        if self.enforce_capabilities:
            capability_error = self._validate_capabilities(request_data, model_metadata)
            if capability_error:
                result["error"] = capability_error
                return result

        return result

    def _infer_provider(self, path: str) -> Literal["anthropic", "openai"] | None:
        """Infer provider from request path.

        Args:
            path: Request path

        Returns:
            Provider name or None
        """
        if "claude" in path.lower() or "/messages" in path:
            return "anthropic"
        if "codex" in path.lower() or "openai" in path.lower():
            return "openai"
        return None

    def _validate_token_limits(
        self,
        request_data: dict[str, Any],
        model_metadata: ModelCard,
        input_tokens: int,
    ) -> dict[str, Any] | None:
        """Validate token limits.

        Args:
            request_data: Request payload
            model_metadata: Model metadata
            input_tokens: Pre-counted input token count

        Returns:
            Error dict if validation fails, None otherwise
        """
        model_id = model_metadata.id

        if model_metadata.max_input_tokens:
            if input_tokens > model_metadata.max_input_tokens:
                return {
                    "message": f"Input exceeds model limit: {input_tokens} tokens sent, but {model_id} supports max {model_metadata.max_input_tokens} input tokens",
                    "type": "invalid_request_error",
                    "param": "messages",
                    "code": "context_length_exceeded",
                }

        requested_output = request_data.get("max_tokens")
        if requested_output and model_metadata.max_output_tokens:
            if requested_output > model_metadata.max_output_tokens:
                return {
                    "message": f"Requested output exceeds model limit: {requested_output} tokens requested, but {model_id} supports max {model_metadata.max_output_tokens} output tokens",
                    "type": "invalid_request_error",
                    "param": "max_tokens",
                    "code": "max_tokens_exceeded",
                }

        return None

    def _check_token_warnings(
        self,
        request_data: dict[str, Any],
        model_metadata: ModelCard,
        input_tokens: int,
    ) -> list[str]:
        """Check if token usage warrants warnings.

        Args:
            request_data: Request payload
            model_metadata: Model metadata
            input_tokens: Pre-counted input token count

        Returns:
            List of warning messages
        """
        warnings: list[str] = []

        if model_metadata.max_input_tokens:
            threshold = model_metadata.max_input_tokens * self.warn_threshold
            if input_tokens > threshold:
                percentage = (input_tokens / model_metadata.max_input_tokens) * 100
                warnings.append(
                    f"Input tokens ({input_tokens}) at {percentage:.1f}% of model limit ({model_metadata.max_input_tokens})"
                )

        return warnings

    def _validate_capabilities(
        self, request_data: dict[str, Any], model_metadata: ModelCard
    ) -> dict[str, Any] | None:
        """Validate model capabilities.

        Args:
            request_data: Request payload
            model_metadata: Model metadata

        Returns:
            Error dict if validation fails, None otherwise
        """
        messages = request_data.get("messages", [])
        has_vision_content = self._has_vision_content(messages)

        if has_vision_content and not model_metadata.supports_vision:
            return {
                "message": f"Model {model_metadata.id} does not support vision/image inputs",
                "type": "invalid_request_error",
                "param": "messages",
                "code": "unsupported_content_type",
            }

        tools = request_data.get("tools")
        functions = request_data.get("functions")
        has_function_calling = bool(tools or functions)

        if has_function_calling and not model_metadata.supports_function_calling:
            return {
                "message": f"Model {model_metadata.id} does not support function calling",
                "type": "invalid_request_error",
                "param": "tools" if tools else "functions",
                "code": "unsupported_feature",
            }

        response_format = request_data.get("response_format")
        if response_format and not model_metadata.supports_response_schema:
            format_type = response_format.get("type")
            if format_type in ["json_object", "json_schema"]:
                return {
                    "message": f"Model {model_metadata.id} does not support structured output",
                    "type": "invalid_request_error",
                    "param": "response_format",
                    "code": "unsupported_feature",
                }

        return None

    def _has_vision_content(self, messages: list[dict[str, Any]]) -> bool:
        """Check if messages contain vision/image content.

        Args:
            messages: List of messages

        Returns:
            True if vision content found
        """
        for message in messages:
            content = message.get("content")
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        item_type = item.get("type")
                        if item_type in ["image_url", "image"]:
                            return True
        return False

    def _create_error_response(self, error: dict[str, Any]) -> Response:
        """Create error response.

        Args:
            error: Error details

        Returns:
            JSON error response
        """
        error_body = {
            "error": {
                "message": error.get("message", "Validation error"),
                "type": error.get("type", "invalid_request_error"),
                "param": error.get("param"),
                "code": error.get("code"),
            }
        }

        return Response(
            content=json.dumps(error_body),
            status_code=400,
            media_type="application/json",
        )
