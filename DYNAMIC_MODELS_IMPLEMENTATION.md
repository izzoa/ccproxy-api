# Dynamic Model Fetching Implementation

## Overview

This implementation replaces hardcoded model lists with dynamic fetching from LiteLLM's `model_prices_and_context_window.json`. Models are now fetched with rich metadata including token limits, capabilities, and provider information.

## Changes Made

### 1. Extended ModelCard Schema (`ccproxy/models/provider.py`)

Added new fields to `ModelCard`:
- `max_input_tokens`, `max_output_tokens`, `max_tokens` - Token limits
- `supports_vision`, `supports_function_calling`, `supports_parallel_function_calling` - Capabilities
- `supports_tool_choice`, `supports_response_schema`, `supports_prompt_caching` - Advanced features
- `supports_system_messages`, `supports_assistant_prefill`, `supports_computer_use` - Anthropic features
- `supports_pdf_input`, `supports_reasoning` - Additional capabilities
- `mode` - Model mode (chat, completion, image_generation)
- `litellm_provider` - Provider identifier from LiteLLM
- `deprecation_date` - Model deprecation date

### 2. Created ModelFetcher Service (`ccproxy/utils/model_fetcher.py`)

Features:
- **Dynamic Fetching**: Fetches model metadata from LiteLLM URL
- **Caching**: Two-tier caching (file cache + in-memory cache)
- **Provider Filtering**: Filter models by provider (anthropic, openai, all)
- **Async Support**: Built on httpx for async operations
- **Error Handling**: Graceful fallback on fetch failures

Key methods:
- `fetch_all_models()` - Fetches all models with caching
- `fetch_models_by_provider()` - Filters by provider and converts to ModelCards
- `_convert_to_model_card()` - Converts LiteLLM format to ModelCard

### 3. Updated Plugin Configurations

#### Codex Config (`ccproxy/plugins/codex/config.py`)
#### Claude API Config (`ccproxy/plugins/claude_api/config.py`)

Added configuration options:
```python
dynamic_models_enabled: bool = True  # Enable/disable dynamic fetching
models_source_url: str = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
models_cache_dir: Path = get_xdg_cache_home() / "ccproxy" / "models"
models_cache_ttl_hours: int = 24  # Cache TTL
models_fetch_timeout: int = 30  # Fetch timeout in seconds
```

### 4. Updated /v1/models Endpoints

#### Codex Routes (`ccproxy/plugins/codex/routes.py`)
#### Claude API Routes (`ccproxy/plugins/claude_api/routes.py`)

Modified `list_models()` endpoints to:
1. Check if `dynamic_models_enabled` is True
2. Create `ModelFetcher` instance with config settings
3. Fetch models by provider (openai for Codex, anthropic for Claude API)
4. Fallback to hardcoded `config.models_endpoint` if fetch fails or disabled

## Usage

### Default Behavior
By default, dynamic model fetching is **enabled**. The `/v1/models` endpoint will:
1. Attempt to fetch from LiteLLM (with caching)
2. Return dynamic models if successful
3. Fallback to hardcoded models if fetch fails

### Disabling Dynamic Fetching
In `config.toml`:
```toml
[plugins.codex]
dynamic_models_enabled = false

[plugins.claude_api]
dynamic_models_enabled = false
```

### Customizing Cache Settings
```toml
[plugins.codex]
models_cache_ttl_hours = 12  # Cache for 12 hours
models_fetch_timeout = 60     # 60 second timeout
```

### Customizing Source URL
```toml
[plugins.codex]
models_source_url = "https://custom-url.com/models.json"
```

## Testing Results

Tested with manual script showing:
- ✅ Successfully fetches 16 Anthropic models
- ✅ Successfully fetches 81 OpenAI models
- ✅ Models include rich metadata (token limits, capabilities)
- ✅ Caching works correctly
- ✅ All linting checks pass

Example output:
```
Model: claude-3-5-sonnet-20241022
  Max Input: 200000
  Max Output: 8192
  Vision: True
  Function Calling: True
  Prompt Caching: True

Model: gpt-5
  Max Input: 272000
  Max Output: 128000
  Vision: True
  Function Calling: True
  Reasoning: True
```

## Benefits

1. **Always Up-to-Date**: Models automatically sync with LiteLLM
2. **Rich Metadata**: Token limits, capabilities, deprecation dates
3. **Performance**: Two-tier caching (file + memory)
4. **Backward Compatible**: Fallback to hardcoded models
5. **Configurable**: Easy to enable/disable or customize
6. **Provider Agnostic**: Works with any provider in LiteLLM

## Architecture

```
/v1/models endpoint
    ↓
Check dynamic_models_enabled
    ↓
ModelFetcher.fetch_models_by_provider()
    ↓
Check memory cache (1 hour TTL)
    ↓
Check file cache (24 hour TTL, configurable)
    ↓
Fetch from LiteLLM URL
    ↓
Filter by provider & mode=chat
    ↓
Convert to ModelCard[]
    ↓
Return to client
    ↓
(On failure: fallback to config.models_endpoint)
```

## Future Enhancements

Possible improvements:
- Background refresh task to keep cache warm
- Webhook support for instant updates
- Model availability checking
- Custom model injection/overrides
- Model usage analytics integration