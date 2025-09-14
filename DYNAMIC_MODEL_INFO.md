# Dynamic Model Information System

## Overview

The CCProxy API has been updated to use dynamic model information fetched from LiteLLM's pricing and context window data, replacing hardcoded constants that become outdated as new models are released.

## Key Changes

### 1. New Components Created

#### `ccproxy/pricing/model_metadata.py`
- `ModelMetadata`: Stores per-model capabilities and limits
- `ModelsMetadata`: Collection of model metadata
- Includes context windows, output limits, and feature support

#### `ccproxy/services/model_info_service.py`
- Central service for accessing dynamic model information
- Fetches data from LiteLLM repository
- Provides fallback values when external source unavailable
- Methods:
  - `get_context_window(model)`: Get total context window
  - `get_max_output_tokens(model)`: Get output token limit
  - `get_model_capabilities(model)`: Get feature support flags
  - `validate_request_tokens(...)`: Validate token limits

#### `ccproxy/adapters/openai/async_adapter.py`
- Async version of OpenAI adapter
- Fetches dynamic defaults at runtime
- Validates requests against model-specific limits

#### `ccproxy/services/enhanced_proxy_service.py`
- Enhanced proxy with dynamic validation
- Sets appropriate defaults based on model
- Rejects requests exceeding model limits

### 2. Updated Components

#### `ccproxy/models/messages.py`
- Relaxed model validation (basic Claude check only)
- Added note that full validation happens at runtime
- Removed hardcoded model list

#### `ccproxy/adapters/openai/adapter.py`
- Changed default max_tokens from 4096 to 8192
- Added comments about using ModelInfoService
- Noted model capability checks should be dynamic

#### `ccproxy/core/constants.py`
- Marked constants as @deprecated:
  - `DEFAULT_MODEL`
  - `DEFAULT_MAX_TOKENS`
  - `MAX_PROMPT_LENGTH`
- Added migration notes for each constant

#### `ccproxy/pricing/loader.py`
- Added `load_metadata_from_data()` method
- Added `load_pricing_and_metadata()` combined loader
- Extracts model capabilities from LiteLLM data

#### `ccproxy/pricing/updater.py`
- Added metadata caching alongside pricing
- `get_current_metadata()` method for fetching metadata
- Integrated metadata updates with pricing updates

## Migration Guide

### Before (Hardcoded Constants)
```python
from ccproxy.core import DEFAULT_MAX_TOKENS, MAX_PROMPT_LENGTH

# Using hardcoded values
max_tokens = DEFAULT_MAX_TOKENS  # Always 4096
if len(prompt) > MAX_PROMPT_LENGTH:  # Always 200,000 characters
    raise ValueError("Prompt too long")
```

### After (Dynamic Model Info)
```python
from ccproxy.services.model_info_service import get_model_info_service

service = get_model_info_service()

# Get model-specific values
max_tokens = await service.get_max_output_tokens("claude-3-5-sonnet-20241022")
context_window = await service.get_context_window("claude-3-5-sonnet-20241022")

# Validate against actual limits
is_valid, error = await service.validate_request_tokens(
    model_name="claude-3-5-sonnet-20241022",
    input_tokens=150000,
    max_output_tokens=4096
)
```

## Benefits

1. **Automatic Updates**: New models are supported without code changes
2. **Accurate Limits**: Each model gets its correct context window and output limits
3. **Feature Detection**: Dynamically determine model capabilities
4. **Reduced Maintenance**: No need to update constants when models change
5. **Better Validation**: Requests validated against actual model limits

## Data Source

The system fetches data from:
```
https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json
```

This includes:
- Context windows (max_input_tokens + max_output_tokens)
- Output limits (max_tokens)
- Pricing information
- Feature support flags (vision, function calling, etc.)

## Testing

Run the demo to see the system in action:
```bash
python examples/dynamic_model_info_demo.py
```

Output shows:
- Dynamic fetching from LiteLLM
- Correct context windows (200k+ tokens)
- Proper output limits (4k-8k tokens)
- Feature capabilities per model

## Fallback Behavior

When the external source is unavailable:
1. Uses cached data if available and recent
2. Falls back to embedded defaults
3. Logs warnings about using fallback values
4. Still provides reasonable defaults for operation

## Future Improvements

1. Add tokenizer integration for accurate token counting
2. Cache model metadata in database for faster access
3. Add webhook support for real-time model updates
4. Implement model deprecation warnings
5. Add support for rate limits and quotas per model