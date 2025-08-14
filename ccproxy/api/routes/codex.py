"""OpenAI Codex API routes."""

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.responses import Response

from ccproxy.adapters.openai.codex_adapter import CodexAdapter
from ccproxy.adapters.openai.models import (
    OpenAIChatCompletionRequest,
)
from ccproxy.api.dependencies import ProxyServiceDep
from ccproxy.auth.openai import OpenAITokenManager
from ccproxy.config.settings import Settings, get_settings


logger = structlog.get_logger(__name__)

# Create router
router = APIRouter(prefix="/codex", tags=["codex"])


def get_token_manager() -> OpenAITokenManager:
    """Get OpenAI token manager dependency."""
    return OpenAITokenManager()


def resolve_session_id(
    path_session: str | None = None,
    header_session: str | None = None,
) -> str:
    """Resolve session ID with priority: path > header > generated."""
    return path_session or header_session or str(uuid.uuid4())


async def check_codex_enabled(settings: Settings = Depends(get_settings)) -> None:
    """Check if Codex is enabled."""
    if not settings.codex.enabled:
        raise HTTPException(
            status_code=503, detail="OpenAI Codex provider is not enabled"
        )


@router.post("/responses", response_model=None)
async def codex_responses(
    request: Request,
    proxy_service: ProxyServiceDep,
    settings: Settings = Depends(get_settings),
    token_manager: OpenAITokenManager = Depends(get_token_manager),
    _: None = Depends(check_codex_enabled),
) -> StreamingResponse | Response:
    """Create completion with auto-generated session_id.

    This endpoint creates a new completion request with an automatically
    generated session_id. Each request gets a unique session.
    """
    from ccproxy.services.provider_context import ProviderContext

    # Get session_id from header if provided
    header_session_id = request.headers.get("session_id")
    session_id = resolve_session_id(header_session=header_session_id)

    # Build provider context
    provider_context = ProviderContext(
        provider_name="codex-native",
        auth_manager=token_manager,
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


@router.post("/{session_id}/responses", response_model=None)
async def codex_responses_with_session(
    session_id: str,
    request: Request,
    proxy_service: ProxyServiceDep,
    settings: Settings = Depends(get_settings),
    token_manager: OpenAITokenManager = Depends(get_token_manager),
    _: None = Depends(check_codex_enabled),
) -> StreamingResponse | Response:
    """Create completion with specific session_id.

    This endpoint creates a completion request using the provided session_id
    from the URL path. This allows for session-specific conversations.
    """
    from ccproxy.services.provider_context import ProviderContext

    # Build provider context with path-provided session_id
    provider_context = ProviderContext(
        provider_name="codex-native",
        auth_manager=token_manager,
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


@router.post("/chat/completions", response_model=None)
async def codex_chat_completions(
    openai_request: OpenAIChatCompletionRequest,
    request: Request,
    proxy_service: ProxyServiceDep,
    settings: Settings = Depends(get_settings),
    token_manager: OpenAITokenManager = Depends(get_token_manager),
    _: None = Depends(check_codex_enabled),
) -> StreamingResponse | Response:
    """OpenAI-compatible chat completions endpoint for Codex.

    This endpoint accepts OpenAI chat/completions format and converts it
    to OpenAI Response API format before forwarding to the ChatGPT backend.
    """
    from ccproxy.services.provider_context import ProviderContext

    # Get session_id from header if provided, otherwise generate
    header_session_id = request.headers.get("session_id")
    session_id = resolve_session_id(header_session=header_session_id)

    # Create adapter for bidirectional conversion
    codex_adapter = CodexAdapter()

    # Build provider context
    provider_context = ProviderContext(
        provider_name="codex",
        auth_manager=token_manager,
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
