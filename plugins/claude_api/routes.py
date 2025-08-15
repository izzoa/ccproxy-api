"""Claude API plugin routes."""

from typing import Any

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from ccproxy.dependencies.auth import ConditionalAuthDep
from ccproxy.dependencies.proxy import ProxyServiceDep
from ccproxy.services.provider_context import ProviderContext


router = APIRouter(tags=["plugin-claude-api"])


def create_anthropic_context(
    provider_name: str,
    proxy_service: Any,
    request_adapter: Any | None = None,
    response_adapter: Any | None = None,
) -> ProviderContext:
    """Create provider context for Anthropic API requests.
    
    Args:
        provider_name: Name of the provider for logging
        proxy_service: Proxy service instance
        request_adapter: Optional request adapter for format conversion
        response_adapter: Optional response adapter for format conversion
        
    Returns:
        ProviderContext configured for Anthropic API
    """
    return ProviderContext(
        provider_name=provider_name,
        auth_manager=proxy_service.credentials_manager,
        target_base_url="https://api.anthropic.com",
        request_adapter=request_adapter,
        response_adapter=response_adapter,
        supports_streaming=True,
        requires_session=False,
    )


@router.post("/v1/messages", response_model=None)
async def create_anthropic_message(
    request: Request,
    proxy_service: ProxyServiceDep,
    auth: ConditionalAuthDep,
) -> Response:
    """Create a message using Claude AI with native Anthropic format.
    
    This endpoint handles Anthropic API format requests and forwards them
    directly to the Claude API without format conversion.
    """
    # Create provider context for native Anthropic format
    context = create_anthropic_context(
        provider_name="claude-api-native",
        proxy_service=proxy_service,
        request_adapter=None,  # No conversion needed
        response_adapter=None,  # Pass through
    )
    
    # Dispatch request through proxy service
    return await proxy_service.dispatch_request(request, context)


@router.post("/v1/chat/completions", response_model=None)
async def create_openai_chat_completion(
    request: Request,
    proxy_service: ProxyServiceDep,
    auth: ConditionalAuthDep,
) -> Response:
    """Create a chat completion using Claude AI with OpenAI-compatible format.
    
    This endpoint handles OpenAI format requests and converts them
    to/from Anthropic format transparently.
    """
    # Get OpenAI adapter for format conversion
    from ccproxy.adapters.openai.adapter import OpenAIAdapter
    
    openai_adapter = OpenAIAdapter()
    
    # Create provider context with OpenAI format conversion
    context = create_anthropic_context(
        provider_name="claude-api-openai",
        proxy_service=proxy_service,
        request_adapter=openai_adapter,
        response_adapter=openai_adapter,
    )
    
    # Dispatch request through proxy service
    return await proxy_service.dispatch_request(request, context)


@router.get("/v1/models", response_model=None)
async def list_models(
    request: Request,
    proxy_service: ProxyServiceDep,
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
        models.append({
            "id": model_id,
            "object": "model",
            "created": 1696000000,  # Placeholder timestamp
            "owned_by": "anthropic",
            "permission": [],
            "root": model_id,
            "parent": None,
        })
    
    return {
        "object": "list",
        "data": models,
    }