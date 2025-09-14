"""Enhanced proxy service with dynamic model info support."""

from typing import Any

import structlog

from ccproxy.services.model_info_service import get_model_info_service
from ccproxy.services.proxy_service import ProxyService

logger = structlog.get_logger(__name__)


class EnhancedProxyService(ProxyService):
    """Enhanced proxy service that uses dynamic model information."""
    
    async def validate_request_with_model_info(
        self, body: dict[str, Any], endpoint: str
    ) -> tuple[bool, str | None]:
        """Validate request against dynamic model limits.
        
        Args:
            body: Request body
            endpoint: API endpoint
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        model_service = get_model_info_service()
        
        # Extract model from request
        model = body.get("model")
        if not model:
            return True, None  # No model to validate
        
        # Extract max_tokens from request
        max_tokens = body.get("max_tokens")
        
        # For Anthropic endpoints
        if endpoint in ["/v1/messages", "/messages"]:
            # Validate max_tokens if provided
            if max_tokens:
                try:
                    max_allowed = await model_service.get_max_output_tokens(model)
                    if max_tokens > max_allowed:
                        return False, (
                            f"Requested max_tokens ({max_tokens}) exceeds "
                            f"model limit ({max_allowed}) for {model}"
                        )
                except Exception as e:
                    logger.warning(
                        "failed_to_validate_max_tokens",
                        model=model,
                        error=str(e),
                    )
            
            # Set default max_tokens if not provided
            if not max_tokens:
                try:
                    default_max = await model_service.get_max_output_tokens(model)
                    body["max_tokens"] = default_max
                    logger.debug(
                        "set_default_max_tokens",
                        model=model,
                        max_tokens=default_max,
                    )
                except Exception as e:
                    logger.warning(
                        "failed_to_get_default_max_tokens",
                        model=model,
                        error=str(e),
                        fallback=8192,
                    )
                    body["max_tokens"] = 8192
        
        # For OpenAI endpoints (handled by adapter)
        elif endpoint in ["/v1/chat/completions", "/openai/v1/chat/completions"]:
            # The async adapter will handle this
            pass
        
        return True, None
    
    async def process_anthropic_request(
        self, body: dict[str, Any], headers: dict[str, str], endpoint: str
    ) -> tuple[dict[str, Any], int, dict[str, str]]:
        """Process Anthropic API request with dynamic validation.
        
        Args:
            body: Request body
            headers: Request headers
            endpoint: API endpoint
            
        Returns:
            Tuple of (response_data, status_code, response_headers)
        """
        # Validate against dynamic model limits
        is_valid, error = await self.validate_request_with_model_info(body, endpoint)
        if not is_valid and error:
            return {"error": {"message": error, "type": "invalid_request"}}, 400, {}
        
        # Continue with normal processing
        return await super().process_anthropic_request(body, headers, endpoint)
    
    async def process_openai_request(
        self, body: dict[str, Any], headers: dict[str, str], endpoint: str
    ) -> tuple[dict[str, Any], int, dict[str, str]]:
        """Process OpenAI API request with dynamic model info.
        
        Args:
            body: Request body
            headers: Request headers
            endpoint: API endpoint
            
        Returns:
            Tuple of (response_data, status_code, response_headers)
        """
        # Use the async adapter if available
        try:
            from ccproxy.adapters.openai.async_adapter import AsyncOpenAIAdapter
            
            # Replace the adapter temporarily if not already async
            if not isinstance(self.openai_adapter, AsyncOpenAIAdapter):
                original_adapter = self.openai_adapter
                self.openai_adapter = AsyncOpenAIAdapter(
                    include_sdk_content_as_xml=getattr(
                        original_adapter, "include_sdk_content_as_xml", False
                    )
                )
                
                # Process the request
                result = await super().process_openai_request(body, headers, endpoint)
                
                # Restore original adapter
                self.openai_adapter = original_adapter
                return result
        except ImportError:
            logger.debug("async_adapter_not_available")
        
        # Fall back to normal processing
        return await super().process_openai_request(body, headers, endpoint)