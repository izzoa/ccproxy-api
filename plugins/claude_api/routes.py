"""Claude API plugin routes."""

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request
from starlette.responses import Response

from ccproxy.api.dependencies import (
    ClaudeAPIAdapterDep,
    ClaudeAPIDetectionDep,
)
from ccproxy.auth.conditional import ConditionalAuthDep
from ccproxy.services.handler_config import HandlerConfig

from .transformers import ClaudeAPIRequestTransformer, ClaudeAPIResponseTransformer


if TYPE_CHECKING:
    pass

router = APIRouter(tags=["plugin-claude-api"])


def claude_api_path_transformer(path: str) -> str:
    """Transform stripped paths for Claude API.

    The path comes in already stripped of the /claude-api prefix,
    so we need to map OpenAI-style paths to Anthropic equivalents.
    """
    # Map OpenAI chat completions to Anthropic messages
    if path == "/v1/chat/completions":
        return "/v1/messages"

    # Pass through native Anthropic paths
    return path


def create_anthropic_context(
    provider_name: str,
    proxy_service: Any,
    detection_service: Any | None = None,
    request_adapter: Any | None = None,
    response_adapter: Any | None = None,
) -> HandlerConfig:
    """Create provider context for Anthropic API requests.

    DEPRECATED: This function is no longer used as the routes now use the adapter pattern.
    Kept for backward compatibility only.

    Args:
        provider_name: Name of the provider for logging
        proxy_service: Proxy service instance
        detection_service: Optional ClaudeAPIDetectionService for transformations
        request_adapter: Optional request adapter for format conversion
        response_adapter: Optional response adapter for format conversion

    Returns:
        HandlerConfig configured for Anthropic API
    """
    # Create transformers with detection service
    request_transformer = None
    response_transformer = None

    if detection_service:
        # Use proper transformers that handle both headers and body
        request_transformer = ClaudeAPIRequestTransformer(detection_service)

    # Always use response transformer to preserve server headers
    response_transformer_obj = ClaudeAPIResponseTransformer()

    return HandlerConfig(
        request_adapter=request_adapter,
        response_adapter=response_adapter,
        request_transformer=request_transformer,
        response_transformer=response_transformer_obj,
        supports_streaming=True,
    )


@router.post("/v1/messages", response_model=None)
async def create_anthropic_message(
    request: Request,
    adapter: ClaudeAPIAdapterDep,
    detection_service: ClaudeAPIDetectionDep,
    auth: ConditionalAuthDep,
) -> Response:
    """Create a message using Claude AI with native Anthropic format.

    This endpoint handles Anthropic API format requests and forwards them
    directly to the Claude API without format conversion.
    """
    # Delegate to adapter which will handle the request properly
    return await adapter.handle_request(request, "/v1/messages", request.method)


@router.post("/v1/chat/completions", response_model=None)
async def create_openai_chat_completion(
    request: Request,
    adapter: ClaudeAPIAdapterDep,
    detection_service: ClaudeAPIDetectionDep,
    auth: ConditionalAuthDep,
) -> Response:
    """Create a chat completion using Claude AI with OpenAI-compatible format.

    This endpoint handles OpenAI format requests and converts them
    to/from Anthropic format transparently.
    """
    # Get OpenAI adapter for format conversion
    from ccproxy.adapters.openai.adapter import OpenAIAdapter

    openai_adapter = OpenAIAdapter()

    # Create chained adapter: OpenAI conversion + Claude transformations
    class ChainedAdapter:
        """Chains OpenAI adapter with Claude transformations."""

        def __init__(self, openai_adapter: Any, claude_transformer: Any):
            self.openai_adapter = openai_adapter
            self.claude_transformer = claude_transformer

        async def adapt_request(self, body: dict[str, Any]) -> dict[str, Any]:
            """Apply OpenAI conversion then Claude transformations."""
            # First convert OpenAI to Anthropic format
            anthropic_body = await self.openai_adapter.adapt_request(body)

            # Then apply Claude transformations (system prompt injection)
            if self.claude_transformer:
                import json

                body_bytes = json.dumps(anthropic_body).encode("utf-8")
                transformed_bytes = self.claude_transformer.transform_body(body_bytes)
                if transformed_bytes:
                    result = json.loads(transformed_bytes.decode("utf-8"))
                    if isinstance(result, dict):
                        return result
                    # If not a dict, return original
                    if isinstance(anthropic_body, dict):
                        return anthropic_body
                    # If neither result nor anthropic_body are dicts, return empty
                    return {}

            # Ensure anthropic_body is a dict
            if isinstance(anthropic_body, dict):
                return anthropic_body
            # If not a dict, return empty dict
            return {}

        async def adapt_response(self, body: dict[str, Any]) -> dict[str, Any]:
            """Pass through to OpenAI adapter for response conversion."""
            result = await self.openai_adapter.adapt_response(body)
            if isinstance(result, dict):
                return result
            # If not a dict, return the original body
            return body

        async def adapt_stream(self, stream: Any) -> Any:
            """Pass through to OpenAI adapter for stream conversion."""
            async for chunk in self.openai_adapter.adapt_stream(stream):
                yield chunk

    # Create chained adapter with injected detection service
    request_adapter: Any = openai_adapter
    response_adapter: Any = openai_adapter
    if detection_service:
        claude_transformer = ClaudeAPIRequestTransformer(detection_service)
        request_adapter = ChainedAdapter(openai_adapter, claude_transformer)

    # Delegate to adapter which will handle the request properly
    return await adapter.handle_request(request, "/v1/chat/completions", request.method)


@router.get("/v1/models", response_model=None)
async def list_models(
    request: Request,
    auth: ConditionalAuthDep,
) -> dict[str, Any]:
    """List available Claude models.

    Returns a list of available models in OpenAI-compatible format.
    """
    # Get configured models from settings
    from ccproxy.config.settings import get_settings

    settings = get_settings()

    # Build OpenAI-compatible model list
    models = []
    model_list = [
        "claude-3-5-sonnet-20241022",
        "claude-3-5-haiku-20241022",
        "claude-3-opus-20240229",
        "claude-3-sonnet-20240229",
        "claude-3-haiku-20240307",
    ]

    for model_id in model_list:
        models.append(
            {
                "id": model_id,
                "object": "model",
                "created": 1696000000,  # Placeholder timestamp
                "owned_by": "anthropic",
                "permission": [],
                "root": model_id,
                "parent": None,
            }
        )

    return {
        "object": "list",
        "data": models,
    }
