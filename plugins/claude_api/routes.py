"""Claude API plugin routes."""

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request
from starlette.responses import Response

from ccproxy.api.dependencies import (
    ClaudeAPIAdapterDep,
    ClaudeAPIDetectionDep,
    ProxyServiceDep,
)
from ccproxy.auth.conditional import ConditionalAuthDep


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


# Note: The create_anthropic_context function has been removed as routes now use
# the adapter pattern via ProxyService.handle_request which enables hook emissions.


@router.post("/v1/messages", response_model=None)
async def create_anthropic_message(
    request: Request,
    adapter: ClaudeAPIAdapterDep,
    proxy_service: ProxyServiceDep,
    detection_service: ClaudeAPIDetectionDep,
    auth: ConditionalAuthDep,
) -> Response:
    """Create a message using Claude AI with native Anthropic format.

    This endpoint handles Anthropic API format requests and forwards them
    directly to the Claude API without format conversion.
    """
    # Use ProxyService.handle_request to enable hook emissions
    return await proxy_service.handle_request(
        request=request,
        endpoint="/v1/messages",
        method=request.method,
        provider="claude_api",
        plugin_name="claude_api",
        adapter_handler=adapter.handle_request,
    )


@router.post("/v1/chat/completions", response_model=None)
async def create_openai_chat_completion(
    request: Request,
    adapter: ClaudeAPIAdapterDep,
    proxy_service: ProxyServiceDep,
    detection_service: ClaudeAPIDetectionDep,
    auth: ConditionalAuthDep,
) -> Response:
    """Create a chat completion using Claude AI with OpenAI-compatible format.

    This endpoint handles OpenAI format requests and converts them
    to/from Anthropic format transparently.
    """
    # Use ProxyService.handle_request to enable hook emissions
    return await proxy_service.handle_request(
        request=request,
        endpoint="/v1/chat/completions",
        method=request.method,
        provider="claude_api",
        plugin_name="claude_api",
        adapter_handler=adapter.handle_request,
    )


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
