#!/usr/bin/env python3
"""Example demonstrating dynamic model information retrieval."""

import asyncio
from ccproxy.services.model_info_service import ModelInfoService


async def main() -> None:
    """Demonstrate dynamic model info retrieval."""
    
    # Create model info service
    service = ModelInfoService.create_default()
    
    # Test models
    test_models = [
        "claude-3-5-sonnet-20241022",
        "claude-3-5-haiku-20241022",
        "claude-3-opus-20240229",
    ]
    
    print("Dynamic Model Information Demo")
    print("=" * 50)
    
    # Get metadata for all models
    print("\nFetching model metadata from external source...")
    metadata = await service.get_model_metadata(force_refresh=True)
    
    if metadata:
        print(f"Loaded metadata for {len(metadata.models)} models")
    else:
        print("Using fallback values (external source unavailable)")
    
    print("\n" + "=" * 50)
    
    for model_name in test_models:
        print(f"\nModel: {model_name}")
        print("-" * 40)
        
        # Get context window
        context_window = await service.get_context_window(model_name)
        print(f"  Context Window: {context_window:,} tokens")
        
        # Get max output tokens
        max_output = await service.get_max_output_tokens(model_name)
        print(f"  Max Output Tokens: {max_output:,}")
        
        # Get capabilities
        capabilities = await service.get_model_capabilities(model_name)
        print(f"  Supports Function Calling: {capabilities['supports_function_calling']}")
        print(f"  Supports Vision: {capabilities['supports_vision']}")
        print(f"  Supports Streaming: {capabilities['supports_streaming']}")
        
        # Validate a sample request
        input_tokens = 150_000
        requested_output = 4096
        is_valid, error = await service.validate_request_tokens(
            model_name, input_tokens, requested_output
        )
        
        print(f"\n  Validation Test:")
        print(f"    Input: {input_tokens:,} tokens, Output: {requested_output:,} tokens")
        print(f"    Valid: {is_valid}")
        if error:
            print(f"    Error: {error}")
    
    print("\n" + "=" * 50)
    print("\nDemo completed!")


if __name__ == "__main__":
    asyncio.run(main())