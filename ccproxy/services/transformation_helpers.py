"""Helper functions for request/response transformations."""

from typing import Any

import structlog

from ccproxy.core.http_transformers import HTTPRequestTransformer


logger = structlog.get_logger(__name__)


async def apply_claude_transformations(
    body: bytes,
    headers: dict[str, str],
    access_token: str,
    app_state: Any = None,
    injection_mode: str = "minimal",
    proxy_mode: str = "full",
) -> tuple[bytes, dict[str, str]]:
    """Apply Claude/Anthropic-specific transformations.

    Args:
        body: Request body to transform
        headers: Request headers to transform
        access_token: OAuth access token for authentication
        app_state: Application state containing detection data
        injection_mode: System prompt injection mode ('minimal' or 'full')
        proxy_mode: Proxy transformation mode

    Returns:
        Tuple of (transformed_body, transformed_headers)
    """
    transformer = HTTPRequestTransformer()

    # Transform body with system prompt injection
    if body:
        logger.debug(
            "applying_claude_body_transformation",
            injection_mode=injection_mode,
            body_size=len(body),
        )
        body = transformer.transform_system_prompt(body, app_state, injection_mode)

    # Transform headers with detected headers
    logger.debug(
        "applying_claude_header_transformation",
        has_detection_data=bool(
            app_state and hasattr(app_state, "claude_detection_data")
        ),
    )
    headers = transformer.create_proxy_headers(
        headers, access_token, proxy_mode, app_state
    )

    return body, headers


def should_apply_claude_transformations(provider_name: str) -> bool:
    """Check if Claude transformations should be applied.

    Args:
        provider_name: Name of the provider

    Returns:
        True if Claude transformations should be applied
    """
    return provider_name in ["claude", "claude-openai", "claude-native"]


