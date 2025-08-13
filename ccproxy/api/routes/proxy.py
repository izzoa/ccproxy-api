"""Proxy endpoints for CCProxy API Server."""

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from ccproxy.adapters.openai.adapter import OpenAIAdapter
from ccproxy.api.dependencies import ProxyServiceDep
from ccproxy.api.shared_handlers import handle_proxy_request
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
    return await handle_proxy_request(
        request=request,
        proxy_service=proxy_service,
        format_converter=OpenAIAdapter(),
    )


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
    return await handle_proxy_request(
        request=request,
        proxy_service=proxy_service,
        format_converter=None,  # Keep Anthropic format as-is
    )
