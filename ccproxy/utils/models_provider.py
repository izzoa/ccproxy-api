"""Shared models provider for CCProxy API Server.

This module provides a centralized source for all available models,
combining Claude and OpenAI models in a consistent format.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ccproxy.services.model_info_service import get_model_info_service


def get_anthropic_models() -> list[dict[str, Any]]:
    """Get list of Anthropic models with metadata (sync version).

    Note: This is a sync wrapper for backward compatibility.
    Use get_anthropic_models_async() for better performance.

    Returns:
        List of Anthropic model entries with type, id, display_name, and created_at fields
    """
    try:
        return asyncio.run(get_anthropic_models_async())
    except Exception:
        # Fallback to static list if dynamic fetching fails
        return _get_fallback_anthropic_models()


async def get_anthropic_models_async() -> list[dict[str, Any]]:
    """Get list of Anthropic models with metadata (async version).

    Returns:
        List of Anthropic model entries with type, id, display_name, and created_at fields
    """
    try:
        model_info_service = get_model_info_service()
        available_models = await model_info_service.get_available_models()
        
        # Filter only Claude models
        claude_models = [model for model in available_models if model.startswith("claude-")]
        
        # Create Anthropic-style model entries
        models = []
        for model_id in claude_models:
            models.append({
                "type": "model",
                "id": model_id,
                "display_name": _get_display_name(model_id),
                "created_at": _get_model_timestamp(model_id),
            })
        
        return models
    except Exception:
        # Fallback to static list if dynamic fetching fails
        return _get_fallback_anthropic_models()


def _get_display_name(model_id: str) -> str:
    """Get display name for a model ID."""
    display_names = {
        "claude-opus-4-20250514": "Claude Opus 4",
        "claude-sonnet-4-20250514": "Claude Sonnet 4",
        "claude-3-7-sonnet-20250219": "Claude Sonnet 3.7",
        "claude-3-5-sonnet-20241022": "Claude Sonnet 3.5 (New)",
        "claude-3-5-haiku-20241022": "Claude Haiku 3.5",
        "claude-3-5-haiku-latest": "Claude Haiku 3.5",
        "claude-3-5-sonnet-20240620": "Claude Sonnet 3.5 (Old)",
        "claude-3-haiku-20240307": "Claude Haiku 3",
        "claude-3-opus-20240229": "Claude Opus 3",
    }
    return display_names.get(model_id, model_id)


def _get_model_timestamp(model_id: str) -> int:
    """Get creation timestamp for a model ID."""
    timestamps = {
        "claude-opus-4-20250514": 1747526400,  # 2025-05-22
        "claude-sonnet-4-20250514": 1747526400,  # 2025-05-22
        "claude-3-7-sonnet-20250219": 1740268800,  # 2025-02-24
        "claude-3-5-sonnet-20241022": 1729555200,  # 2024-10-22
        "claude-3-5-haiku-20241022": 1729555200,  # 2024-10-22
        "claude-3-5-haiku-latest": 1729555200,  # 2024-10-22
        "claude-3-5-sonnet-20240620": 1718841600,  # 2024-06-20
        "claude-3-haiku-20240307": 1709769600,  # 2024-03-07
        "claude-3-opus-20240229": 1709164800,  # 2024-02-29
    }
    return timestamps.get(model_id, 1677610602)  # Default timestamp


def _get_fallback_anthropic_models() -> list[dict[str, Any]]:
    """Fallback static list of Anthropic models."""
    return [
        {
            "type": "model",
            "id": "claude-3-5-sonnet-20241022",
            "display_name": "Claude Sonnet 3.5 (New)",
            "created_at": 1729555200,
        },
        {
            "type": "model",
            "id": "claude-3-5-haiku-20241022",
            "display_name": "Claude Haiku 3.5",
            "created_at": 1729555200,
        },
        {
            "type": "model",
            "id": "claude-3-haiku-20240307",
            "display_name": "Claude Haiku 3",
            "created_at": 1709769600,
        },
        {
            "type": "model",
            "id": "claude-3-opus-20240229",
            "display_name": "Claude Opus 3",
            "created_at": 1709164800,
        },
    ]


def get_openai_models() -> list[dict[str, Any]]:
    """Get list of recent OpenAI models with metadata (sync version).

    Note: This is a sync wrapper for backward compatibility.
    Use get_openai_models_async() for better performance.

    Returns:
        List of OpenAI model entries with id, object, created, and owned_by fields
    """
    try:
        return asyncio.run(get_openai_models_async())
    except Exception:
        # Fallback to static list if dynamic fetching fails
        return _get_fallback_openai_models()


async def get_openai_models_async() -> list[dict[str, Any]]:
    """Get list of OpenAI models with metadata (async version).

    Returns:
        List of OpenAI model entries with id, object, created, and owned_by fields
    """
    try:
        model_info_service = get_model_info_service()
        available_models = await model_info_service.get_available_models()
        
        # Filter only OpenAI models (or use known OpenAI patterns)
        openai_models = [model for model in available_models if any(
            model.startswith(prefix) for prefix in ["gpt-", "o1-", "o1", "o3-", "o3"]
        )]
        
        # Create OpenAI-style model entries
        models = []
        for model_id in openai_models:
            models.append({
                "id": model_id,
                "object": "model",
                "created": _get_openai_model_timestamp(model_id),
                "owned_by": "openai",
            })
        
        return models
    except Exception:
        # Fallback to static list if dynamic fetching fails
        return _get_fallback_openai_models()


def _get_openai_model_timestamp(model_id: str) -> int:
    """Get creation timestamp for an OpenAI model ID."""
    timestamps = {
        "gpt-4o": 1715367049,
        "gpt-4o-mini": 1721172741,
        "gpt-4-turbo": 1712361441,
        "gpt-4-turbo-preview": 1706037777,
        "o1": 1734375816,
        "o1-mini": 1725649008,
        "o1-preview": 1725648897,
        "o3": 1744225308,
        "o3-mini": 1737146383,
    }
    return timestamps.get(model_id, 1677610602)  # Default timestamp


def _get_fallback_openai_models() -> list[dict[str, Any]]:
    """Fallback static list of OpenAI models."""
    return [
        {
            "id": "gpt-4o",
            "object": "model",
            "created": 1715367049,
            "owned_by": "openai",
        },
        {
            "id": "gpt-4o-mini",
            "object": "model",
            "created": 1721172741,
            "owned_by": "openai",
        },
        {
            "id": "gpt-4-turbo",
            "object": "model",
            "created": 1712361441,
            "owned_by": "openai",
        },
        {
            "id": "o1",
            "object": "model",
            "created": 1734375816,
            "owned_by": "openai",
        },
        {
            "id": "o1-mini",
            "object": "model",
            "created": 1725649008,
            "owned_by": "openai",
        },
        {
            "id": "o3",
            "object": "model",
            "created": 1744225308,
            "owned_by": "openai",
        },
        {
            "id": "o3-mini",
            "object": "model",
            "created": 1737146383,
            "owned_by": "openai",
        },
    ]


def get_models_list() -> dict[str, Any]:
    """Get combined list of available Claude and OpenAI models (sync version).

    Note: This is a sync wrapper for backward compatibility.
    Use get_models_list_async() for better performance.

    Returns:
        Dictionary with combined list of models in mixed format compatible with both
        Anthropic and OpenAI API specifications
    """
    try:
        return asyncio.run(get_models_list_async())
    except Exception:
        # Fallback to static lists if dynamic fetching fails
        anthropic_models = _get_fallback_anthropic_models()
        openai_models = _get_fallback_openai_models()
        return {
            "data": anthropic_models + openai_models,
            "has_more": False,
            "object": "list",
        }


async def get_models_list_async() -> dict[str, Any]:
    """Get combined list of available Claude and OpenAI models (async version).

    Returns:
        Dictionary with combined list of models in mixed format compatible with both
        Anthropic and OpenAI API specifications
    """
    try:
        anthropic_models = await get_anthropic_models_async()
        openai_models = await get_openai_models_async()

        # Return combined response in mixed format
        return {
            "data": anthropic_models + openai_models,
            "has_more": False,
            "object": "list",
        }
    except Exception:
        # Fallback to static lists if dynamic fetching fails
        anthropic_models = _get_fallback_anthropic_models()
        openai_models = _get_fallback_openai_models()
        return {
            "data": anthropic_models + openai_models,
            "has_more": False,
            "object": "list",
        }


__all__ = [
    "get_anthropic_models",
    "get_anthropic_models_async", 
    "get_openai_models",
    "get_openai_models_async",
    "get_models_list",
    "get_models_list_async",
]
