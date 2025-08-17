"""Codex plugin routes."""

import uuid
from typing import Any

from fastapi import APIRouter, Request
from starlette.responses import Response, StreamingResponse

from ccproxy.api.dependencies import ProxyServiceDep
from ccproxy.auth.conditional import ConditionalAuthDep


router = APIRouter(tags=["plugin-codex"])


def codex_path_transformer(path: str) -> str:
    """Transform stripped paths for Codex API.

    The path comes in already stripped of the /codex prefix.
    Maps various endpoint patterns to the Codex /responses endpoint.
    """
    # Map chat completions to Codex responses
    if path == "/chat/completions" or path == "/v1/chat/completions":
        return "/responses"

    # Map OpenAI-style completions to Codex responses
    if path == "/completions" or path == "/v1/completions":
        return "/responses"

    # For everything else, just return as-is
    return path


@router.post("/responses", response_model=None)
async def codex_responses(
    request: Request,
    proxy_service: ProxyServiceDep,
    auth: ConditionalAuthDep,
) -> StreamingResponse | Response:
    """Create Codex completion with auto-generated session_id.

    Creates a provider context and delegates to proxy service.
    """
    from ccproxy.services.provider_context import ProviderContext

    # Get the codex plugin for auth manager
    plugin = None
    auth_manager = None
    if hasattr(proxy_service, "plugin_manager"):
        plugin = proxy_service.plugin_manager.plugin_registry.get_plugin("codex")
        adapter = proxy_service.plugin_manager.get_plugin_adapter("codex")
        if adapter and hasattr(adapter, "_auth_manager"):
            auth_manager = adapter._auth_manager

    if not plugin:
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail="Codex plugin not initialized")

    if not auth_manager:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=503, detail="Codex auth manager not initialized"
        )

    # Get session_id from header if provided
    header_session_id = request.headers.get("session_id")
    session_id = header_session_id or str(uuid.uuid4())

    # Create provider context for codex
    context = ProviderContext(
        provider_name="codex",
        auth_manager=auth_manager,
        target_base_url="https://chatgpt.com",
        route_prefix="/codex",
        supports_streaming=True,
        requires_session=True,
        session_id=session_id,
        path_transformer=lambda p: "/backend-api/codex/responses",
    )

    # Delegate to proxy service which will handle via the adapter
    result = await proxy_service.dispatch_request(request, context)
    return result


@router.post("/{session_id}/responses", response_model=None)
async def codex_responses_with_session(
    session_id: str,
    request: Request,
    proxy_service: ProxyServiceDep,
    auth: ConditionalAuthDep,
) -> StreamingResponse | Response:
    """Create Codex completion with specific session_id.

    Creates a provider context and delegates to proxy service.
    """
    from ccproxy.services.provider_context import ProviderContext

    # Get the codex plugin for auth manager
    plugin = None
    auth_manager = None
    if hasattr(proxy_service, "plugin_manager"):
        plugin = proxy_service.plugin_manager.plugin_registry.get_plugin("codex")
        adapter = proxy_service.plugin_manager.get_plugin_adapter("codex")
        if adapter and hasattr(adapter, "_auth_manager"):
            auth_manager = adapter._auth_manager

    if not plugin:
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail="Codex plugin not initialized")

    if not auth_manager:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=503, detail="Codex auth manager not initialized"
        )

    # Create provider context for codex
    context = ProviderContext(
        provider_name="codex",
        auth_manager=auth_manager,
        target_base_url="https://chatgpt.com",
        route_prefix="/codex",
        supports_streaming=True,
        requires_session=True,
        session_id=session_id,
        path_transformer=lambda p: "/backend-api/codex/responses",
    )

    # Delegate to proxy service which will handle via the adapter
    result = await proxy_service.dispatch_request(request, context)
    return result


@router.post("/chat/completions", response_model=None)
async def codex_chat_completions(
    request: Request,
    proxy_service: ProxyServiceDep,
    auth: ConditionalAuthDep,
) -> StreamingResponse | Response:
    """Create a chat completion using Codex with OpenAI-compatible format.

    This endpoint handles OpenAI format requests and converts them
    to/from Codex Response API format transparently.
    """
    from ccproxy.services.provider_context import ProviderContext

    # Get the codex plugin for auth manager
    plugin = None
    auth_manager = None
    if hasattr(proxy_service, "plugin_manager"):
        plugin = proxy_service.plugin_manager.plugin_registry.get_plugin("codex")
        adapter = proxy_service.plugin_manager.get_plugin_adapter("codex")
        if adapter and hasattr(adapter, "_auth_manager"):
            auth_manager = adapter._auth_manager

    if not plugin:
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail="Codex plugin not initialized")

    if not auth_manager:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=503, detail="Codex auth manager not initialized"
        )

    # Get session_id from header if provided
    header_session_id = request.headers.get("session_id")
    session_id = header_session_id or str(uuid.uuid4())

    # Create provider context for codex with format conversion
    context = ProviderContext(
        provider_name="codex",
        auth_manager=auth_manager,
        target_base_url="https://chatgpt.com",
        route_prefix="/codex",
        supports_streaming=True,
        requires_session=True,
        session_id=session_id,
        path_transformer=lambda p: "/backend-api/codex/responses",
    )

    # Delegate to proxy service which will handle via the adapter
    result = await proxy_service.dispatch_request(request, context)
    return result


@router.post("/{session_id}/chat/completions", response_model=None)
async def codex_chat_completions_with_session(
    session_id: str,
    request: Request,
    proxy_service: ProxyServiceDep,
    auth: ConditionalAuthDep,
) -> StreamingResponse | Response:
    """Create a chat completion with specific session_id using OpenAI format.

    This endpoint handles OpenAI format requests with a specific session_id.
    """
    from ccproxy.services.provider_context import ProviderContext

    # Get the codex plugin for auth manager
    plugin = None
    auth_manager = None
    if hasattr(proxy_service, "plugin_manager"):
        plugin = proxy_service.plugin_manager.plugin_registry.get_plugin("codex")
        adapter = proxy_service.plugin_manager.get_plugin_adapter("codex")
        if adapter and hasattr(adapter, "_auth_manager"):
            auth_manager = adapter._auth_manager

    if not plugin:
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail="Codex plugin not initialized")

    if not auth_manager:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=503, detail="Codex auth manager not initialized"
        )

    # Create provider context for codex with format conversion
    context = ProviderContext(
        provider_name="codex",
        auth_manager=auth_manager,
        target_base_url="https://chatgpt.com",
        route_prefix="/codex",
        supports_streaming=True,
        requires_session=True,
        session_id=session_id,
        path_transformer=lambda p: "/backend-api/codex/responses",
    )

    # Delegate to proxy service which will handle via the adapter
    result = await proxy_service.dispatch_request(request, context)
    return result


@router.post("/v1/chat/completions", response_model=None)
async def codex_v1_chat_completions(
    request: Request,
    proxy_service: ProxyServiceDep,
    auth: ConditionalAuthDep,
) -> StreamingResponse | Response:
    """OpenAI v1 compatible chat completions endpoint.

    Maps to the standard chat completions handler.
    """
    return await codex_chat_completions(request, proxy_service, auth)


@router.get("/v1/models", response_model=None)
async def list_models(
    request: Request,
    proxy_service: ProxyServiceDep,
    auth: ConditionalAuthDep,
) -> dict[str, Any]:
    """List available Codex models.

    Returns a list of available models in OpenAI-compatible format.
    """
    # Build OpenAI-compatible model list
    models = []
    model_list = [
        "gpt-5",
        "gpt-5-2025-08-07",
        "gpt-5-mini",
        "gpt-5-mini-2025-08-07",
        "gpt-5-nano",
        "gpt-5-nano-2025-08-07",
    ]

    for model_id in model_list:
        models.append(
            {
                "id": model_id,
                "object": "model",
                "created": 1704000000,  # Placeholder timestamp
                "owned_by": "openai",
                "permission": [],
                "root": model_id,
                "parent": None,
            }
        )

    return {
        "object": "list",
        "data": models,
    }
