"""Helper functions for request/response transformations."""

from typing import Any

import structlog

from ccproxy.core.codex_transformers import CodexRequestTransformer
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


async def apply_codex_transformations(
    body: bytes,
    headers: dict[str, str],
    access_token: str,
    session_id: str,
    account_id: str = "",
    app_state: Any = None,
) -> tuple[bytes, dict[str, str]]:
    """Apply Codex/OpenAI-specific transformations.

    Args:
        body: Request body to transform
        headers: Request headers to transform
        access_token: OAuth access token for authentication
        session_id: Codex session ID
        account_id: ChatGPT account ID
        app_state: Application state containing detection data

    Returns:
        Tuple of (transformed_body, transformed_headers)
    """
    transformer = CodexRequestTransformer()

    # Get detection data
    codex_detection_data = None
    if app_state and hasattr(app_state, "codex_detection_data"):
        codex_detection_data = app_state.codex_detection_data
        logger.debug(
            "using_codex_detection_data", version=codex_detection_data.codex_version
        )

    # Transform body with instructions injection
    if body:
        logger.debug(
            "applying_codex_body_transformation",
            has_detection_data=bool(codex_detection_data),
            body_size=len(body),
        )
        body = transformer.transform_codex_body(body, codex_detection_data)

    # Transform headers with Codex CLI identity
    logger.debug(
        "applying_codex_header_transformation",
        session_id=session_id,
        has_account_id=bool(account_id),
    )
    headers = transformer.create_codex_headers(
        headers, access_token, session_id, account_id, body, codex_detection_data
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


def should_apply_codex_transformations(provider_name: str) -> bool:
    """Check if Codex transformations should be applied.

    Args:
        provider_name: Name of the provider

    Returns:
        True if Codex transformations should be applied
    """
    return provider_name in ["codex", "codex-native"]
