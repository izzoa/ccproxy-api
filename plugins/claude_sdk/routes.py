"""Routes for Claude SDK plugin."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from ccproxy.api.dependencies import get_plugin_adapter
from ccproxy.auth.conditional import ConditionalAuthDep


# Create plugin-specific adapter dependency
ClaudeSDKAdapterDep = Annotated[Any, Depends(get_plugin_adapter("claude_sdk"))]

# Create router for Claude SDK endpoints
router = APIRouter(tags=["plugin-claude_sdk"])


@router.post("/v1/messages")
async def claude_sdk_messages(
    request: Request,
    auth: ConditionalAuthDep,
    adapter: ClaudeSDKAdapterDep,
) -> Any:
    """Handle Anthropic-format messages endpoint via Claude SDK.

    Args:
        request: FastAPI request object
        auth: Conditional authentication dependency
        adapter: Claude SDK adapter dependency

    Returns:
        Response from Claude SDK
    """

    return await adapter.handle_request(
        request=request,
        endpoint="/v1/messages",
        method=request.method,
    )


@router.post("/v1/chat/completions")
async def claude_sdk_chat_completions(
    request: Request,
    auth: ConditionalAuthDep,
    adapter: ClaudeSDKAdapterDep,
) -> Any:
    """Handle OpenAI-format chat completions endpoint via Claude SDK.

    Args:
        request: FastAPI request object
        auth: Conditional authentication dependency
        adapter: Claude SDK adapter dependency

    Returns:
        Response from Claude SDK in OpenAI format
    """

    return await adapter.handle_request(
        request=request,
        endpoint="/v1/chat/completions",
        method=request.method,
    )


@router.post("/{session_id}/v1/messages")
async def claude_sdk_messages_with_session(
    request: Request,
    session_id: str,
    auth: ConditionalAuthDep,
    adapter: ClaudeSDKAdapterDep,
) -> Any:
    """Handle Anthropic-format messages endpoint via Claude SDK with session ID in path.

    Args:
        request: FastAPI request object
        session_id: Session ID from URL path
        auth: Conditional authentication dependency
        adapter: Claude SDK adapter dependency

    Returns:
        Response from Claude SDK
    """
    # Store session_id in request state for the adapter to access
    request.state.session_id = session_id

    return await adapter.handle_request(
        request=request,
        endpoint=f"/{session_id}/v1/messages",
        method=request.method,
    )


@router.post("/{session_id}/v1/chat/completions")
async def claude_sdk_chat_completions_with_session(
    request: Request,
    session_id: str,
    auth: ConditionalAuthDep,
    adapter: ClaudeSDKAdapterDep,
) -> Any:
    """Handle OpenAI-format chat completions endpoint via Claude SDK with session ID in path.

    Args:
        request: FastAPI request object
        session_id: Session ID from URL path
        auth: Conditional authentication dependency
        adapter: Claude SDK adapter dependency

    Returns:
        Response from Claude SDK in OpenAI format
    """
    # Store session_id in request state for the adapter to access
    request.state.session_id = session_id

    return await adapter.handle_request(
        request=request,
        endpoint=f"/{session_id}/v1/chat/completions",
        method=request.method,
    )
