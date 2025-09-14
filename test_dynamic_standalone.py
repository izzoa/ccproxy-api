#!/usr/bin/env python3
"""Standalone test for dynamic model info - no module imports."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Direct imports without going through __init__
from ccproxy.pricing.model_metadata import ModelMetadata, ModelsMetadata
from ccproxy.pricing.loader import PricingLoader

def test_model_metadata():
    """Test model metadata structures."""
    print("Testing Model Metadata")
    print("=" * 50)
    
    # Create sample metadata
    metadata = ModelMetadata(
        max_input_tokens=200000,
        max_output_tokens=8192,
        max_tokens=200000,
        supports_function_calling=True,
        supports_vision=True,
        supports_streaming=True,
    )
    
    print(f"Created metadata:")
    print(f"  Max input: {metadata.max_input_tokens:,}")
    print(f"  Max output: {metadata.max_output_tokens:,}")
    print(f"  Context window: {metadata.max_tokens:,}")
    print(f"  Supports functions: {metadata.supports_function_calling}")
    print(f"  Supports vision: {metadata.supports_vision}")
    
    # Test ModelsMetadata collection
    models_metadata = ModelsMetadata(
        models={
            "claude-3-5-sonnet-20241022": metadata,
            "claude-3-5-haiku-20241022": ModelMetadata(
                max_input_tokens=200000,
                max_output_tokens=8192,
                max_tokens=200000,
                supports_function_calling=True,
                supports_vision=False,
                supports_streaming=True,
            ),
        }
    )
    
    print(f"\nModels in collection: {models_metadata.model_names()}")
    
    # Test retrieval methods
    sonnet_tokens = models_metadata.get_max_tokens("claude-3-5-sonnet-20241022")
    print(f"\nSonnet context window: {sonnet_tokens:,}")
    
    unknown_tokens = models_metadata.get_max_tokens("unknown-model", default=100000)
    print(f"Unknown model (with default): {unknown_tokens:,}")


def test_litellm_conversion():
    """Test conversion from LiteLLM format."""
    print("\n\nTesting LiteLLM Data Conversion")
    print("=" * 50)
    
    # Simulate LiteLLM data structure
    sample_litellm_data = {
        "claude-3-5-sonnet-20241022": {
            "litellm_provider": "anthropic",
            "max_tokens": 200000,
            "max_input_tokens": 200000,
            "max_output_tokens": 8192,
            "input_cost_per_token": 0.000003,
            "output_cost_per_token": 0.000015,
            "supports_function_calling": True,
            "supports_vision": True,
            "mode": "chat",
        },
        "claude-3-opus-20240229": {
            "litellm_provider": "anthropic",
            "max_tokens": 200000,
            "max_input_tokens": 200000,
            "max_output_tokens": 4096,
            "input_cost_per_token": 0.000015,
            "output_cost_per_token": 0.000075,
            "supports_function_calling": True,
            "supports_vision": True,
            "mode": "chat",
        },
        "gpt-4": {
            "litellm_provider": "openai",
            "max_tokens": 8192,
            "max_input_tokens": 8192,
            "max_output_tokens": 4096,
            "input_cost_per_token": 0.00003,
            "output_cost_per_token": 0.00006,
        },
    }
    
    # Extract Claude models
    claude_models = PricingLoader.extract_claude_models(sample_litellm_data, verbose=False)
    print(f"Found {len(claude_models)} Claude models")
    
    # Create metadata from LiteLLM data
    metadata = ModelsMetadata.from_litellm_data(sample_litellm_data)
    print(f"Created metadata for {len(metadata.models)} models")
    
    for model_name in metadata.model_names():
        model_info = metadata.get(model_name)
        if model_info:
            print(f"\n{model_name}:")
            print(f"  Context: {model_info.max_tokens:,} tokens")
            print(f"  Max output: {model_info.max_output_tokens:,} tokens")
            print(f"  Vision: {model_info.supports_vision}")


def test_deprecated_constants():
    """Show how deprecated constants are marked."""
    print("\n\nDeprecated Constants Status")
    print("=" * 50)
    
    from ccproxy.core.constants import DEFAULT_MODEL, DEFAULT_MAX_TOKENS, MAX_PROMPT_LENGTH
    
    print(f"DEFAULT_MODEL = {DEFAULT_MODEL}")
    print("  Status: @deprecated - Use ModelInfoService.get_default_model()")
    
    print(f"\nDEFAULT_MAX_TOKENS = {DEFAULT_MAX_TOKENS}")
    print("  Status: @deprecated - Use ModelInfoService.get_max_output_tokens()")
    
    print(f"\nMAX_PROMPT_LENGTH = {MAX_PROMPT_LENGTH:,}")
    print("  Status: @deprecated - Use ModelInfoService.get_context_window()")
    print("  Note: This was in characters, not tokens!")


if __name__ == "__main__":
    print("=" * 60)
    print("Dynamic Model Info System - Standalone Test")
    print("=" * 60)
    
    test_model_metadata()
    test_litellm_conversion()
    test_deprecated_constants()
    
    print("\n" + "=" * 60)
    print("All tests completed successfully!")
    print("=" * 60)