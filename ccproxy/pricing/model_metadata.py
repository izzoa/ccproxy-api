"""Model metadata including context windows and capabilities."""

from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class ModelMetadata(BaseModel):
    """Metadata for a single Claude model including capabilities and limits."""
    
    # Context window information
    max_input_tokens: int = Field(..., ge=0, description="Maximum input tokens")
    max_output_tokens: int = Field(..., ge=0, description="Maximum output tokens")
    max_tokens: int = Field(..., ge=0, description="Total context window")
    
    # Additional metadata from LiteLLM
    supports_function_calling: bool = Field(
        default=False, description="Whether model supports function/tool calling"
    )
    supports_vision: bool = Field(
        default=False, description="Whether model supports image inputs"
    )
    supports_streaming: bool = Field(
        default=True, description="Whether model supports streaming responses"
    )
    
    # Model information
    litellm_provider: str = Field(
        default="anthropic", description="Provider identifier in LiteLLM"
    )
    mode: str = Field(
        default="chat", description="Model mode (chat, completion, etc.)"
    )
    
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="allow"  # Allow additional fields from LiteLLM
    )


class ModelsMetadata(BaseModel):
    """Collection of model metadata keyed by model name."""
    
    models: dict[str, ModelMetadata] = Field(
        default_factory=dict, description="Model metadata by name"
    )
    
    def get(self, model_name: str) -> ModelMetadata | None:
        """Get metadata for a specific model."""
        return self.models.get(model_name)
    
    def get_max_tokens(self, model_name: str, default: int = 200_000) -> int:
        """Get maximum context window for a model.
        
        Args:
            model_name: Name of the model
            default: Default value if model not found
            
        Returns:
            Maximum context window size
        """
        metadata = self.get(model_name)
        return metadata.max_tokens if metadata else default
    
    def get_max_output_tokens(self, model_name: str, default: int = 4096) -> int:
        """Get maximum output tokens for a model.
        
        Args:
            model_name: Name of the model
            default: Default value if model not found
            
        Returns:
            Maximum output tokens
        """
        metadata = self.get(model_name)
        return metadata.max_output_tokens if metadata else default
    
    def model_names(self) -> list[str]:
        """Get list of all model names."""
        return list(self.models.keys())
    
    @classmethod
    def from_litellm_data(cls, litellm_data: dict[str, Any]) -> "ModelsMetadata":
        """Create ModelsMetadata from LiteLLM data format.
        
        Args:
            litellm_data: Raw LiteLLM model data
            
        Returns:
            ModelsMetadata instance
        """
        models = {}
        
        for model_name, model_data in litellm_data.items():
            if not isinstance(model_data, dict):
                continue
                
            # Only process Anthropic models
            if model_data.get("litellm_provider") != "anthropic":
                continue
            
            # Extract metadata fields
            # Note: In LiteLLM, max_tokens is the output limit, not context window
            # The full context window is max_input_tokens + max_output_tokens
            max_input = model_data.get("max_input_tokens", 200_000)
            max_output = model_data.get("max_output_tokens", model_data.get("max_tokens", 4096))
            
            metadata = ModelMetadata(
                max_input_tokens=max_input,
                max_output_tokens=max_output,
                max_tokens=max_input + max_output,  # Total context window
                supports_function_calling=model_data.get("supports_function_calling", False),
                supports_vision=model_data.get("supports_vision", False),
                supports_streaming=model_data.get("supports_streaming", True),
                litellm_provider=model_data.get("litellm_provider", "anthropic"),
                mode=model_data.get("mode", "chat"),
            )
            
            models[model_name] = metadata
        
        return cls(models=models)