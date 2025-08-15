"""Claude API plugin routes."""

from typing import Any

from fastapi import APIRouter, Request
from starlette.responses import Response

from ccproxy.api.dependencies import ProxyServiceDep
from ccproxy.auth.conditional import ConditionalAuthDep
from ccproxy.services.provider_context import ProviderContext

from .transformers import ClaudeAPIRequestTransformer, ClaudeAPIResponseTransformer


router = APIRouter(tags=["plugin-claude-api"])


def claude_api_path_transformer(path: str) -> str:
    """Transform stripped paths for Claude API.

    The path comes in already stripped of the /claude-api prefix,
    so we need to map OpenAI-style paths to Anthropic equivalents.
    """
    # Map OpenAI chat completions to Anthropic messages
    if path == "/v1/chat/completions":
        return "/v1/messages"
    
    # Pass through native Anthropic paths
    return path


def create_anthropic_context(
    provider_name: str,
    proxy_service: Any,
    detection_service: Any | None = None,
    request_adapter: Any | None = None,
    response_adapter: Any | None = None,
) -> ProviderContext:
    """Create provider context for Anthropic API requests.

    Args:
        provider_name: Name of the provider for logging
        proxy_service: Proxy service instance
        detection_service: Optional ClaudeAPIDetectionService for transformations
        request_adapter: Optional request adapter for format conversion
        response_adapter: Optional response adapter for format conversion

    Returns:
        ProviderContext configured for Anthropic API
    """
    # Create transformers with detection service
    request_transformer = None
    response_transformer = None
    
    if detection_service:
        # Use proper transformers that handle both headers and body
        transformer = ClaudeAPIRequestTransformer(detection_service)
        request_transformer = lambda headers: transformer.transform_headers(headers)
    
    # Always use response transformer to preserve server headers
    response_transformer_obj = ClaudeAPIResponseTransformer()
    
    return ProviderContext(
        provider_name=provider_name,
        auth_manager=proxy_service.credentials_manager,
        target_base_url="https://api.anthropic.com",
        request_adapter=request_adapter,
        response_adapter=response_adapter,
        supports_streaming=True,
        requires_session=False,
        path_transformer=claude_api_path_transformer,
        request_transformer=request_transformer,
        route_prefix="/claude-api",
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
    # Get detection service from plugin registry
    detection_service = None
    if hasattr(proxy_service, "plugin_registry"):
        plugin = proxy_service.plugin_registry.get_plugin("claude_api")
        if plugin and hasattr(plugin, "_detection_service"):
            detection_service = plugin._detection_service

    # Create request transformer for header and body transformation
    request_adapter = None
    if detection_service:
        request_adapter = ClaudeAPIRequestTransformer(detection_service)

    # Create provider context for native Anthropic format
    context = create_anthropic_context(
        provider_name="claude-api-native",
        proxy_service=proxy_service,
        detection_service=detection_service,
        request_adapter=request_adapter,
        response_adapter=None,  # Pass through native format
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
    # Get detection service from plugin registry
    detection_service = None
    if hasattr(proxy_service, "plugin_registry"):
        plugin = proxy_service.plugin_registry.get_plugin("claude_api")
        if plugin and hasattr(plugin, "_detection_service"):
            detection_service = plugin._detection_service

    # Get OpenAI adapter for format conversion
    from ccproxy.adapters.openai.adapter import OpenAIAdapter

    openai_adapter = OpenAIAdapter()

    # Create chained adapter: OpenAI conversion + Claude transformations
    class ChainedAdapter:
        """Chains OpenAI adapter with Claude transformations."""
        
        def __init__(self, openai_adapter: Any, claude_transformer: Any):
            self.openai_adapter = openai_adapter
            self.claude_transformer = claude_transformer
        
        async def adapt_request(self, body: dict[str, Any]) -> dict[str, Any]:
            """Apply OpenAI conversion then Claude transformations."""
            # First convert OpenAI to Anthropic format
            anthropic_body = await self.openai_adapter.adapt_request(body)
            
            # Then apply Claude transformations (system prompt injection)
            if self.claude_transformer:
                import json
                body_bytes = json.dumps(anthropic_body).encode("utf-8")
                transformed_bytes = self.claude_transformer.transform_body(body_bytes)
                if transformed_bytes:
                    return json.loads(transformed_bytes.decode("utf-8"))
            
            return anthropic_body
        
        async def adapt_response(self, body: dict[str, Any]) -> dict[str, Any]:
            """Pass through to OpenAI adapter for response conversion."""
            return await self.openai_adapter.adapt_response(body)
        
        async def adapt_stream(self, stream):
            """Pass through to OpenAI adapter for stream conversion."""
            async for chunk in self.openai_adapter.adapt_stream(stream):
                yield chunk

    # Create chained adapter if we have detection service
    request_adapter = openai_adapter
    if detection_service:
        claude_transformer = ClaudeAPIRequestTransformer(detection_service)
        request_adapter = ChainedAdapter(openai_adapter, claude_transformer)

    # Create provider context with OpenAI format conversion
    context = create_anthropic_context(
        provider_name="claude-api-openai",
        proxy_service=proxy_service,
        detection_service=detection_service,
        request_adapter=request_adapter,
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
        models.append(
            {
                "id": model_id,
                "object": "model",
                "created": 1696000000,  # Placeholder timestamp
                "owned_by": "anthropic",
                "permission": [],
                "root": model_id,
                "parent": None,
            }
        )

    return {
        "object": "list",
        "data": models,
    }