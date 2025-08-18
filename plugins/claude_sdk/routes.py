"""Routes for Claude SDK plugin."""

from typing import Any

from fastapi import APIRouter, Request

from ccproxy.api.dependencies import (
    ClaudeSDKAdapterDep,
)
from ccproxy.auth.conditional import ConditionalAuthDep


# Create router for Claude SDK endpoints
router = APIRouter(tags=["plugin-claude_sdk"])


@router.post("/v1/messages")
async def claude_sdk_messages(
    request: Request,
    adapter: ClaudeSDKAdapterDep,
    auth: ConditionalAuthDep,
) -> Any:
    """Handle Anthropic-format messages endpoint via Claude SDK.

    Args:
        request: FastAPI request object
        adapter: Claude SDK adapter dependency
        auth: Conditional authentication dependency

    Returns:
        Response from Claude SDK
    """
    # Delegate to adapter which will handle the request properly
    return await adapter.handle_request(request, "/v1/messages", request.method)


@router.post("/v1/chat/completions")
async def claude_sdk_chat_completions(
    request: Request,
    adapter: ClaudeSDKAdapterDep,
    auth: ConditionalAuthDep,
) -> Any:
    """Handle OpenAI-format chat completions endpoint via Claude SDK.

    Args:
        request: FastAPI request object
        adapter: Claude SDK adapter dependency
        auth: Conditional authentication dependency

    Returns:
        Response from Claude SDK in OpenAI format
    """
    # Delegate to adapter which will handle the request properly
    return await adapter.handle_request(request, "/v1/chat/completions", request.method)


@router.post("/{session_id}/v1/messages")
async def claude_sdk_messages_with_session(
    request: Request,
    session_id: str,
    adapter: ClaudeSDKAdapterDep,
    auth: ConditionalAuthDep,
) -> Any:
    """Handle Anthropic-format messages endpoint via Claude SDK with session ID in path.

    Args:
        request: FastAPI request object
        session_id: Session ID from URL path
        adapter: Claude SDK adapter dependency
        auth: Conditional authentication dependency

    Returns:
        Response from Claude SDK
    """
    # Store session_id in request state for the adapter to access
    request.state.session_id = session_id

    # Delegate to adapter which will handle the request properly
    return await adapter.handle_request(
        request, f"/{session_id}/v1/messages", request.method
    )


@router.post("/{session_id}/v1/chat/completions")
async def claude_sdk_chat_completions_with_session(
    request: Request,
    session_id: str,
    adapter: ClaudeSDKAdapterDep,
    auth: ConditionalAuthDep,
) -> Any:
    """Handle OpenAI-format chat completions endpoint via Claude SDK with session ID in path.

    Args:
        request: FastAPI request object
        session_id: Session ID from URL path
        adapter: Claude SDK adapter dependency
        auth: Conditional authentication dependency

    Returns:
        Response from Claude SDK in OpenAI format
    """
    # Store session_id in request state for the adapter to access
    request.state.session_id = session_id

    # Delegate to adapter which will handle the request properly
    return await adapter.handle_request(
        request, f"/{session_id}/v1/chat/completions", request.method
    )
