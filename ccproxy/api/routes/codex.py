"""OpenAI Codex API routes."""

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.responses import Response

from ccproxy.api.dependencies import ProxyServiceDep
from ccproxy.auth.openai import OpenAITokenManager
from ccproxy.config.settings import Settings, get_settings
from ccproxy.core.errors import AuthenticationError, ProxyError


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
    # Get session_id from header if provided
    header_session_id = request.headers.get("session_id")
    session_id = resolve_session_id(header_session=header_session_id)

    # Get and validate access token
    try:
        access_token = await token_manager.get_valid_token()
        if not access_token:
            raise HTTPException(
                status_code=401,
                detail="No valid OpenAI credentials found. Please authenticate first.",
            )
    except Exception as e:
        logger.error("Failed to get OpenAI access token", error=str(e))
        raise HTTPException(
            status_code=401, detail="Failed to retrieve valid credentials"
        ) from e

    try:
        # Handle the Codex request
        response = await proxy_service.handle_codex_request(
            method="POST",
            path="/responses",
            session_id=session_id,
            access_token=access_token,
            request=request,
            settings=settings,
        )
        return response
    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    except ProxyError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        logger.error("Unexpected error in codex_responses", error=str(e))
        raise HTTPException(status_code=500, detail="Internal server error") from e


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
    # Get and validate access token
    try:
        access_token = await token_manager.get_valid_token()
        if not access_token:
            raise HTTPException(
                status_code=401,
                detail="No valid OpenAI credentials found. Please authenticate first.",
            )
    except Exception as e:
        logger.error("Failed to get OpenAI access token", error=str(e))
        raise HTTPException(
            status_code=401, detail="Failed to retrieve valid credentials"
        ) from e

    try:
        # Handle the Codex request with specific session_id
        response = await proxy_service.handle_codex_request(
            method="POST",
            path=f"/{session_id}/responses",
            session_id=session_id,
            access_token=access_token,
            request=request,
            settings=settings,
        )
        return response
    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    except ProxyError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        logger.error("Unexpected error in codex_responses_with_session", error=str(e))
        raise HTTPException(status_code=500, detail="Internal server error") from e
