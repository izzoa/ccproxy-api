#!/usr/bin/env python3
"""Integration test for dynamic model info system."""

import asyncio
import json
from typing import Any

from ccproxy.adapters.openai.async_adapter import AsyncOpenAIAdapter
from ccproxy.models.messages import MessageCreateParams
from ccproxy.services.enhanced_proxy_service import EnhancedProxyService
from ccproxy.services.model_info_service import ModelInfoService


async def test_model_validation() -> None:
    """Test dynamic model validation."""
    print("\n=== Testing Model Validation ===")
    
    # Test with a valid model (relaxed validation)
    try:
        params = MessageCreateParams(
            model="claude-3-5-sonnet-20241022",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=1000,
        )
        print(f"✓ Model validation passed for: {params.model}")
    except Exception as e:
        print(f"✗ Model validation failed: {e}")
    
    # Test with invalid model name
    try:
        params = MessageCreateParams(
            model="gpt-4",  # Not a Claude model
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=1000,
        )
        print(f"✗ Should have failed for non-Claude model: {params.model}")
    except ValueError as e:
        print(f"✓ Correctly rejected non-Claude model: {e}")
    
    # Test max_tokens validation (relaxed, happens at runtime)
    try:
        params = MessageCreateParams(
            model="claude-3-5-sonnet-20241022",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=150000,  # High but not over 200k
        )
        print(f"✓ Max tokens validation deferred to runtime: {params.max_tokens}")
    except Exception as e:
        print(f"Validation error: {e}")


async def test_openai_adapter() -> None:
    """Test OpenAI adapter with dynamic defaults."""
    print("\n=== Testing OpenAI Adapter ===")
    
    adapter = AsyncOpenAIAdapter()
    
    # Test request without max_tokens (should get dynamic default)
    request = {
        "model": "gpt-4",
        "messages": [{"role": "user", "content": "Hello"}],
    }
    
    result = await adapter.adapt_request_async(request)
    print(f"✓ Adapted request has max_tokens: {result.get('max_tokens')}")
    print(f"  Model mapped to: {result.get('model')}")
    
    # Test with o3 model (should enable thinking)
    request = {
        "model": "o3",
        "messages": [{"role": "user", "content": "Solve this problem"}],
    }
    
    result = await adapter.adapt_request_async(request)
    if "thinking" in result:
        print(f"✓ Thinking enabled for o3 model")
        print(f"  Budget tokens: {result['thinking'].get('budget_tokens')}")
    else:
        print("✗ Thinking not enabled for o3 model")


async def test_model_info_service() -> None:
    """Test the model info service directly."""
    print("\n=== Testing Model Info Service ===")
    
    service = ModelInfoService.create_default()
    
    models_to_test = [
        "claude-3-5-sonnet-20241022",
        "claude-3-5-haiku-20241022",
        "claude-3-opus-20240229",
    ]
    
    for model in models_to_test:
        print(f"\nModel: {model}")
        
        # Get context window
        context = await service.get_context_window(model)
        print(f"  Context window: {context:,} tokens")
        
        # Get max output
        max_out = await service.get_max_output_tokens(model)
        print(f"  Max output: {max_out:,} tokens")
        
        # Get capabilities
        caps = await service.get_model_capabilities(model)
        print(f"  Capabilities:")
        print(f"    - Function calling: {caps['supports_function_calling']}")
        print(f"    - Vision: {caps['supports_vision']}")
        print(f"    - Streaming: {caps['supports_streaming']}")
        
        # Validate a request
        is_valid, error = await service.validate_request_tokens(
            model, 
            input_tokens=100000,
            max_output_tokens=4096
        )
        print(f"  Validation (100k in, 4k out): {'✓ Valid' if is_valid else f'✗ {error}'}")


async def test_enhanced_proxy_service() -> None:
    """Test the enhanced proxy service."""
    print("\n=== Testing Enhanced Proxy Service ===")
    
    # This would normally be initialized with proper config
    # For testing, we'll just show the validation logic
    
    # Test Anthropic request validation
    body = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": "Hello"}],
        # No max_tokens - should get default
    }
    
    service = EnhancedProxyService(
        base_url="https://api.anthropic.com",
        api_key="test-key",
    )
    
    is_valid, error = await service.validate_request_with_model_info(
        body, "/v1/messages"
    )
    
    if is_valid:
        print(f"✓ Request validated")
        if "max_tokens" in body:
            print(f"  Default max_tokens set: {body['max_tokens']}")
    else:
        print(f"✗ Validation failed: {error}")
    
    # Test with excessive max_tokens
    body = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": "Hello"}],
        "max_tokens": 500000,  # Way too high
    }
    
    is_valid, error = await service.validate_request_with_model_info(
        body, "/v1/messages"
    )
    
    if not is_valid:
        print(f"✓ Correctly rejected excessive max_tokens: {error}")
    else:
        print(f"✗ Should have rejected excessive max_tokens")


async def main() -> None:
    """Run all integration tests."""
    print("=" * 60)
    print("Dynamic Model Info Integration Tests")
    print("=" * 60)
    
    await test_model_validation()
    await test_openai_adapter()
    await test_model_info_service()
    await test_enhanced_proxy_service()
    
    print("\n" + "=" * 60)
    print("Integration tests completed!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())