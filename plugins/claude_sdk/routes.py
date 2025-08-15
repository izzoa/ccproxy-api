"""Routes for Claude SDK plugin."""

from fastapi import APIRouter, Request
from starlette.responses import Response

from ccproxy.api.dependencies import ProxyServiceDep
from ccproxy.auth.conditional import ConditionalAuthDep
from ccproxy.services.provider_context import ProviderContext


# Create router for Claude SDK endpoints
router = APIRouter(tags=["plugin-claude_sdk"])


def _path_transformer(path: str) -> str:
    """Transform paths for Claude SDK routing.

    The Claude SDK uses the same endpoints as Anthropic API.

    Args:
        path: Original request path

    Returns:
        Transformed path for Claude SDK
    """
    # Claude SDK uses the same paths as Anthropic API
    return path


@router.post("/v1/messages")
async def claude_sdk_messages(
    request: Request,
    proxy_service: ProxyServiceDep,
    auth: ConditionalAuthDep,
) -> Response:
    """Handle Anthropic-format messages endpoint via Claude SDK.

    Args:
        request: FastAPI request object
        proxy_service: Proxy service dependency
        auth: Conditional authentication dependency

    Returns:
        Response from Claude SDK
    """
    # Create provider context for Claude SDK
    # Use proxy service's credentials manager as placeholder (Claude SDK handles auth internally)
    context = ProviderContext(
        provider_name="claude_sdk",
        auth_manager=proxy_service.credentials_manager,
        target_base_url="claude-sdk://local",  # Special URL for SDK
        route_prefix="/claude",
        path_transformer=_path_transformer,
        timeout=300.0,  # 5 minute timeout for SDK operations
        supports_streaming=True,
    )

    return await proxy_service.dispatch_request(request, context)


@router.post("/v1/chat/completions")
async def claude_sdk_chat_completions(
    request: Request,
    proxy_service: ProxyServiceDep,
    auth: ConditionalAuthDep,
) -> Response:
    """Handle OpenAI-format chat completions endpoint via Claude SDK.

    Args:
        request: FastAPI request object
        proxy_service: Proxy service dependency
        auth: Conditional authentication dependency

    Returns:
        Response from Claude SDK in OpenAI format
    """
    # Create provider context for Claude SDK with OpenAI compatibility
    # Use proxy service's credentials manager as placeholder (Claude SDK handles auth internally)
    context = ProviderContext(
        provider_name="claude_sdk",
        auth_manager=proxy_service.credentials_manager,
        target_base_url="claude-sdk://local",  # Special URL for SDK
        route_prefix="/claude",
        path_transformer=_path_transformer,
        timeout=300.0,  # 5 minute timeout for SDK operations
        supports_streaming=True,
    )

    return await proxy_service.dispatch_request(request, context)
