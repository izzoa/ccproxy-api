"""Async OpenAI API adapter with dynamic model info support."""

from __future__ import annotations

from typing import Any

import structlog

from ccproxy.services.model_info_service import get_model_info_service
from ccproxy.utils.model_mapping import map_model_to_claude

from .adapter import OpenAIAdapter
from .models import OpenAIChatCompletionRequest

logger = structlog.get_logger(__name__)


class AsyncOpenAIAdapter(OpenAIAdapter):
    """Async OpenAI adapter with dynamic model info support."""
    
    async def adapt_request_async(self, request: dict[str, Any]) -> dict[str, Any]:
        """Convert OpenAI request format to Anthropic format with dynamic model info.
        
        This async version can fetch dynamic model information at runtime.
        
        Args:
            request: OpenAI format request
            
        Returns:
            Anthropic format request with dynamic defaults
        """
        # Parse the request to get the model
        try:
            openai_req = OpenAIChatCompletionRequest(**request)
        except Exception as e:
            # Fall back to sync version if parsing fails
            return self.adapt_request(request)
        
        # Map to Claude model
        claude_model = map_model_to_claude(openai_req.model)
        
        # Get model info service
        model_service = get_model_info_service()
        
        # If max_tokens is not specified, get model-specific default
        if openai_req.max_tokens is None:
            try:
                # Get the model's default max output tokens
                default_max_tokens = await model_service.get_max_output_tokens(claude_model)
                request["max_tokens"] = default_max_tokens
                logger.debug(
                    "dynamic_max_tokens_set",
                    model=claude_model,
                    max_tokens=default_max_tokens,
                )
            except Exception as e:
                logger.warning(
                    "failed_to_get_dynamic_max_tokens",
                    model=claude_model,
                    error=str(e),
                    fallback=8192,
                )
                request["max_tokens"] = 8192
        
        # Check if model supports thinking/reasoning
        if openai_req.model.startswith(("o1", "o3")):
            try:
                capabilities = await model_service.get_model_capabilities(claude_model)
                if not capabilities.get("supports_function_calling", True):
                    logger.warning(
                        "model_may_not_support_thinking",
                        model=claude_model,
                        capabilities=capabilities,
                    )
            except Exception as e:
                logger.debug(
                    "failed_to_get_model_capabilities",
                    model=claude_model,
                    error=str(e),
                )
        
        # Validate token limits
        if openai_req.max_tokens:
            try:
                is_valid, error = await model_service.validate_request_tokens(
                    claude_model,
                    input_tokens=0,  # We don't have tokenized input here
                    max_output_tokens=openai_req.max_tokens,
                )
                if not is_valid and error:
                    logger.warning(
                        "token_validation_warning",
                        model=claude_model,
                        requested_max_tokens=openai_req.max_tokens,
                        error=error,
                    )
            except Exception as e:
                logger.debug(
                    "failed_to_validate_tokens",
                    model=claude_model,
                    error=str(e),
                )
        
        # Use the synchronous adapter for the actual conversion
        # with our potentially modified request
        return self.adapt_request(request)