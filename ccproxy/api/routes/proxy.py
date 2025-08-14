"""Proxy endpoints for CCProxy API Server."""

import uuid

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from ccproxy.adapters.openai.adapter import OpenAIAdapter
from ccproxy.adapters.openai.codex_adapter import CodexAdapter
from ccproxy.api.dependencies import ProxyServiceDep
from ccproxy.auth.conditional import ConditionalAuthDep
from ccproxy.auth.openai import OpenAITokenManager


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


@router.post("/codex/responses", response_model=None)
async def codex_responses(
    request: Request,
    proxy_service: ProxyServiceDep,
    auth: ConditionalAuthDep,
) -> StreamingResponse | Response:
    """Create Codex completion with auto-generated session_id.

    This endpoint creates a new completion request with an automatically
    generated session_id. Each request gets a unique session.
    """
    from ccproxy.config.settings import get_settings
    from ccproxy.services.provider_context import ProviderContext

    settings = get_settings()

    # Get session_id from header if provided
    header_session_id = request.headers.get("session_id")
    session_id = header_session_id or str(uuid.uuid4())

    # Use plugin dispatch through the codex plugin
    provider_context = ProviderContext(
        provider_name="codex-native",
        auth_manager=OpenAITokenManager(),
        target_base_url=settings.codex.base_url,
        request_adapter=None,  # No conversion needed for native API
        response_adapter=None,  # Pass through
        session_id=session_id,
        supports_streaming=True,
        requires_session=True,
        extra_headers={"session_id": session_id},
    )

    # Dispatch to unified handler
    return await proxy_service.dispatch_request(request, provider_context)


@router.post("/codex/{session_id}/responses", response_model=None)
async def codex_responses_with_session(
    session_id: str,
    request: Request,
    proxy_service: ProxyServiceDep,
    auth: ConditionalAuthDep,
) -> StreamingResponse | Response:
    """Create Codex completion with specific session_id.

    This endpoint creates a completion request using the provided session_id
    from the URL path. This allows for session-specific conversations.
    """
    from ccproxy.config.settings import get_settings
    from ccproxy.services.provider_context import ProviderContext

    settings = get_settings()

    # Build provider context with path-provided session_id
    provider_context = ProviderContext(
        provider_name="codex-native",
        auth_manager=OpenAITokenManager(),
        target_base_url=settings.codex.base_url,
        request_adapter=None,  # No conversion needed for native API
        response_adapter=None,  # Pass through
        session_id=session_id,
        supports_streaming=True,
        requires_session=True,
        extra_headers={"session_id": session_id},
    )

    # Dispatch to unified handler
    return await proxy_service.dispatch_request(request, provider_context)


@router.post("/codex/chat/completions", response_model=None)
async def codex_chat_completions(
    request: Request,
    proxy_service: ProxyServiceDep,
    auth: ConditionalAuthDep,
) -> StreamingResponse | Response:
    """OpenAI-compatible chat completions endpoint for Codex.

    This endpoint accepts OpenAI chat/completions format and converts it
    to OpenAI Response API format before forwarding to the ChatGPT backend.
    """
    from ccproxy.config.settings import get_settings
    from ccproxy.services.provider_context import ProviderContext

    settings = get_settings()

    # Get session_id from header if provided, otherwise generate
    header_session_id = request.headers.get("session_id")
    session_id = header_session_id or str(uuid.uuid4())

    # Create adapter for bidirectional conversion
    codex_adapter = CodexAdapter()

    # Build provider context with format conversion
    provider_context = ProviderContext(
        provider_name="codex",
        auth_manager=OpenAITokenManager(),
        target_base_url=settings.codex.base_url,
        request_adapter=codex_adapter,
        response_adapter=codex_adapter,
        session_id=session_id,
        supports_streaming=True,
        requires_session=True,
        extra_headers={
            "session_id": session_id,
            "accept": "text/event-stream",
        },
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


