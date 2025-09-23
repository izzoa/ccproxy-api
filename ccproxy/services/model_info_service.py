"""Service for providing dynamic model information including context windows."""

from typing import Any

from structlog import get_logger

from ccproxy.config.pricing import PricingSettings
from ccproxy.pricing.cache import PricingCache
from ccproxy.pricing.model_metadata import ModelsMetadata
from ccproxy.pricing.updater import PricingUpdater

logger = get_logger(__name__)


class ModelInfoService:
    """Service for accessing dynamic model information."""
    
    def __init__(self, pricing_updater: PricingUpdater | None = None) -> None:
        """Initialize the model info service.
        
        Args:
            pricing_updater: Optional pricing updater instance
        """
        self._updater = pricing_updater
        self._fallback_context_windows = {
            # Claude 3.5 models
            "claude-3-5-sonnet-20241022": 200_000,
            "claude-3-5-haiku-20241022": 200_000,
            "claude-3-5-sonnet-20240620": 200_000,
            # Claude 3 models
            "claude-3-opus-20240229": 200_000,
            "claude-3-sonnet-20240229": 200_000,
            "claude-3-haiku-20240307": 200_000,
            # Future Claude 4 models (assumed)
            "claude-opus-4-20250514": 200_000,
            "claude-sonnet-4-20250514": 200_000,
            "claude-3-7-sonnet-20250219": 200_000,
            # OpenAI Response API models (fallback estimates)
            "gpt-5": 200_000,
            "gpt-4o": 128_000,
            "gpt-4o-mini": 128_000,
            "o1": 64_000,
            "o1-mini": 64_000,
            "o1-preview": 64_000,
            "o3-mini": 64_000,
        }
        self._fallback_output_limits = {
            # Most models default to 4096
            "claude-3-5-sonnet-20241022": 8192,
            "claude-3-5-haiku-20241022": 8192,
            "claude-3-5-sonnet-20240620": 8192,
            "claude-3-opus-20240229": 4096,
            "claude-3-sonnet-20240229": 4096,
            "claude-3-haiku-20240307": 4096,
            # OpenAI Response API fallbacks
            "gpt-5": 8192,
            "gpt-4o": 8192,
            "gpt-4o-mini": 8192,
            "o1": 4096,
            "o1-mini": 4096,
            "o1-preview": 4096,
            "o3-mini": 4096,
        }
    
    @classmethod
    def create_default(cls) -> "ModelInfoService":
        """Create a default model info service with standard configuration."""
        settings = PricingSettings()
        cache = PricingCache(settings)
        updater = PricingUpdater(cache, settings)
        return cls(pricing_updater=updater)
    
    async def get_model_metadata(self, force_refresh: bool = False) -> ModelsMetadata | None:
        """Get current model metadata.
        
        Args:
            force_refresh: Force refresh from external source
            
        Returns:
            Model metadata or None if unavailable
        """
        if self._updater:
            return await self._updater.get_current_metadata(force_refresh=force_refresh)
        return None
    
    async def get_context_window(self, model_name: str) -> int:
        """Get the context window size for a model.
        
        Args:
            model_name: Name of the model
            
        Returns:
            Context window size in tokens
        """
        # Try to get from dynamic metadata
        metadata = await self.get_model_metadata()
        if metadata:
            model_info = metadata.get(model_name)
            if model_info:
                return model_info.max_tokens
        
        # Fallback to hardcoded values
        return self._fallback_context_windows.get(model_name, 200_000)
    
    async def get_max_output_tokens(self, model_name: str) -> int:
        """Get the maximum output tokens for a model.
        
        Args:
            model_name: Name of the model
            
        Returns:
            Maximum output tokens
        """
        # Try to get from dynamic metadata
        metadata = await self.get_model_metadata()
        if metadata:
            model_info = metadata.get(model_name)
            if model_info:
                return model_info.max_output_tokens
        
        # Fallback to hardcoded values
        return self._fallback_output_limits.get(model_name, 4096)
    
    async def get_model_capabilities(self, model_name: str) -> dict[str, Any]:
        """Get capabilities for a model.
        
        Args:
            model_name: Name of the model
            
        Returns:
            Dictionary of model capabilities
        """
        # Try to get from dynamic metadata
        metadata = await self.get_model_metadata()
        if metadata:
            model_info = metadata.get(model_name)
            if model_info:
                return {
                    "supports_function_calling": model_info.supports_function_calling,
                    "supports_vision": model_info.supports_vision,
                    "supports_streaming": model_info.supports_streaming,
                    "max_tokens": model_info.max_tokens,
                    "max_output_tokens": model_info.max_output_tokens,
                    "max_input_tokens": model_info.max_input_tokens,
                }
        
        # Fallback response
        return {
            "supports_function_calling": True,  # Most Claude models support this
            "supports_vision": "vision" in model_name or "claude-3" in model_name,
            "supports_streaming": True,
            "max_tokens": self._fallback_context_windows.get(model_name, 200_000),
            "max_output_tokens": self._fallback_output_limits.get(model_name, 4096),
            "max_input_tokens": self._fallback_context_windows.get(model_name, 200_000),
        }
    
    async def validate_request_tokens(
        self, model_name: str, input_tokens: int, max_output_tokens: int | None = None
    ) -> tuple[bool, str | None]:
        """Validate if a request fits within model limits.
        
        Args:
            model_name: Name of the model
            input_tokens: Number of input tokens
            max_output_tokens: Requested max output tokens
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        context_window = await self.get_context_window(model_name)
        max_output = await self.get_max_output_tokens(model_name)
        
        # Check input tokens
        if input_tokens > context_window:
            return False, f"Input tokens ({input_tokens}) exceed model's context window ({context_window})"
        
        # Check output tokens if specified
        if max_output_tokens:
            if max_output_tokens > max_output:
                return False, f"Requested output tokens ({max_output_tokens}) exceed model's limit ({max_output})"
            
            # Check total doesn't exceed context window
            total = input_tokens + max_output_tokens
            if total > context_window:
                return False, f"Total tokens ({total}) exceed model's context window ({context_window})"
        
        return True, None
    
    async def get_available_models(self) -> list[str]:
        """Get list of available model names.
        
        Returns:
            List of available model names
        """
        try:
            metadata = await self.get_model_metadata()
            if metadata:
                return metadata.model_names()
        except Exception as e:
            logger.warning("failed_to_get_available_models", error=str(e))
        
        # Return fallback list
        return list(self._fallback_context_windows.keys())

    async def get_default_model(self) -> str:
        """Get the default model name.
        
        Returns:
            Default model name (typically the latest Sonnet model)
        """
        try:
            models = await self.get_available_models()
            # Prefer the latest Sonnet model if available
            preferred_models = [
                "claude-3-5-sonnet-20241022",
                "claude-3-5-sonnet-20240620",
                "claude-3-opus-20240229",
            ]
            for model in preferred_models:
                if model in models:
                    return model
            
            # If no preferred models, return the first Claude model
            claude_models = [m for m in models if m.startswith("claude-")]
            if claude_models:
                return claude_models[0]
            
            # Final fallback
            return "claude-3-5-sonnet-20241022"
        except Exception as e:
            logger.warning("failed_to_get_default_model", error=str(e))
            return "claude-3-5-sonnet-20241022"


# Global instance for easy access
_model_info_service: ModelInfoService | None = None


def get_model_info_service() -> ModelInfoService:
    """Get or create the global model info service."""
    global _model_info_service
    if _model_info_service is None:
        _model_info_service = ModelInfoService.create_default()
    return _model_info_service


def set_model_info_service(service: ModelInfoService) -> None:
    """Set the global model info service."""
    global _model_info_service
    _model_info_service = service
