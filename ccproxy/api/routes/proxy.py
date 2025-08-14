"""Proxy endpoints for CCProxy API Server."""

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from ccproxy.adapters.openai.adapter import OpenAIAdapter
from ccproxy.api.dependencies import ProxyServiceDep
from ccproxy.auth.conditional import ConditionalAuthDep


# Create the router for proxy endpoints
router = APIRouter(tags=["proxy"])


@router.post("/v1/chat/completions", response_model=None)
async def create_openai_chat_completion(
    request: Request,
    proxy_service: ProxyServiceDep,
    auth: ConditionalAuthDep,
) -> StreamingResponse | Response:
    """Create a chat completion using Claude AI with OpenAI-compatible format.

    This endpoint handles OpenAI API format requests and forwards them
    directly to Claude via the proxy service.
    """
    from ccproxy.config.settings import get_settings
    from ccproxy.services.provider_context import ProviderContext

    settings = get_settings()

    # Create adapter for format conversion
    openai_adapter = OpenAIAdapter()

    # Build provider context
    provider_context = ProviderContext(
        provider_name="claude-openai",
        auth_manager=proxy_service.credentials_manager,
        target_base_url="https://api.anthropic.com",
        request_adapter=openai_adapter,
        response_adapter=openai_adapter,
        supports_streaming=True,
        requires_session=False,
    )

    # Dispatch to unified handler
    return await proxy_service.dispatch_request(request, provider_context)



@router.post("/v1/messages", response_model=None)
async def create_anthropic_message(
    request: Request,
    proxy_service: ProxyServiceDep,
    auth: ConditionalAuthDep,
) -> StreamingResponse | Response:
    """Create a message using Claude AI with Anthropic format.

    This endpoint handles Anthropic API format requests and forwards them
    directly to Claude via the proxy service.
    """
    from ccproxy.config.settings import get_settings
    from ccproxy.services.provider_context import ProviderContext

    settings = get_settings()

    # Build provider context (no adapters needed for native format)
    provider_context = ProviderContext(
        provider_name="claude-native",
        auth_manager=proxy_service.credentials_manager,
        target_base_url="https://api.anthropic.com",
        request_adapter=None,  # No conversion needed
        response_adapter=None,  # Pass through
        supports_streaming=True,
        requires_session=False,
    )

    # Dispatch to unified handler
    return await proxy_service.dispatch_request(request, provider_context)


