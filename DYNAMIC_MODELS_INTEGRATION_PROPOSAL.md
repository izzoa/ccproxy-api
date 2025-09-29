# Dynamic Models Full Integration Proposal

## Current State

### What Works ✅
- `/v1/models` endpoint returns latest models from LiteLLM
- Models include rich metadata (token limits, capabilities)
- Two-tier caching (file + memory)
- Fallback to hardcoded models

### What's Missing ❌
- **Request processing doesn't use model metadata**
- **No token limit validation**
- **No capability checking** (vision, function calling, etc.)
- **Model metadata not available in request context**

## Problem

Currently, the dynamic model metadata is **only cosmetic** - it's returned to clients via `/v1/models`, but ccproxy doesn't actually use it when processing requests. This means:

1. Clients see `max_input_tokens: 200000` for Claude Sonnet
2. But ccproxy won't validate if a request exceeds this limit
3. Clients see `supports_vision: true`
4. But ccproxy won't warn if vision content is sent to a non-vision model

## Proposed Solution

### Phase 1: Model Registry (Foundation)

Create a centralized model registry that:
- Caches fetched models by provider
- Provides O(1) lookup by model ID
- Auto-refreshes based on TTL
- Thread-safe for concurrent access

```python
# ccproxy/utils/model_registry.py

class ModelRegistry:
    """Centralized registry for model metadata."""

    async def get_model(self, model_id: str, provider: str) -> ModelCard | None:
        """Get model metadata by ID."""

    async def validate_request(self, model_id: str, request_data: dict) -> ValidationResult:
        """Validate request against model capabilities."""

    async def refresh(self) -> None:
        """Refresh model metadata from LiteLLM."""
```

### Phase 2: Request Validation Middleware

Add middleware that validates requests before processing:

```python
# ccproxy/api/middleware/model_validation.py

async def model_validation_middleware(request: Request, call_next):
    """Validate request against model metadata."""

    # 1. Extract model ID from request body
    # 2. Look up model in registry
    # 3. Validate:
    #    - Input token count <= max_input_tokens
    #    - Vision content only if supports_vision
    #    - Function calls only if supports_function_calling
    # 4. Add warnings to response headers if limits approached
    # 5. Return 400 if hard limits exceeded
```

### Phase 3: Context Integration

Add model metadata to request context:

```python
# In request context
class RequestContext:
    model_metadata: ModelCard | None = None  # Add this field

# In adapter
async def handle_request(self, request: Request):
    ctx = request.state.context

    # Look up model metadata
    model_id = extract_model_from_body(body)
    ctx.model_metadata = await self.model_registry.get_model(model_id, provider="anthropic")

    # Now formatters can access model capabilities
    if ctx.model_metadata and not ctx.model_metadata.supports_vision:
        # Strip vision content or warn
        pass
```

### Phase 4: Token Counting

Integrate token counting for validation:

```python
# Use tiktoken for OpenAI models
# Use anthropic's counting for Claude models

async def count_tokens(messages: list, model: str) -> int:
    """Count tokens for validation."""

async def validate_token_limits(request_data: dict, model_metadata: ModelCard) -> bool:
    """Check if request fits within model's token limits."""
    input_tokens = await count_tokens(request_data['messages'], model_metadata.id)
    max_tokens = request_data.get('max_tokens', 4096)

    total_estimate = input_tokens + max_tokens

    if model_metadata.max_input_tokens:
        if input_tokens > model_metadata.max_input_tokens:
            raise TokenLimitExceeded(...)
```

## Architecture

```
Client Request
    ↓
Model Validation Middleware
    ↓
1. Extract model ID
2. Look up in ModelRegistry
3. Count input tokens
4. Validate against limits
5. Check capabilities
    ↓
Request Context (with model_metadata)
    ↓
Format Chain
    ↓
Adapter (can access model_metadata)
    ↓
Upstream Provider
```

## Implementation Plan

### Minimal Integration (Quick Win)
**Goal:** Make request processing aware of model metadata without breaking changes

1. Add `ModelRegistry` singleton
2. Initialize on startup with dynamic models
3. Add `model_metadata` field to `RequestContext`
4. Look up model in adapters and attach to context
5. Log warnings if limits approached (no enforcement yet)

**Files to modify:**
- `ccproxy/utils/model_registry.py` (new)
- `ccproxy/core/request_context.py` (add field)
- `ccproxy/services/adapters/http_adapter.py` (look up model)
- `ccproxy/api/app.py` (initialize registry on startup)

### Full Integration (Complete Solution)
**Goal:** Full validation and enforcement

All minimal changes PLUS:
1. Add token counting utilities
2. Add validation middleware
3. Enforce token limits (return 400 if exceeded)
4. Check capabilities (warn/strip unsupported features)
5. Add response headers with usage info

**Additional files:**
- `ccproxy/api/middleware/model_validation.py` (new)
- `ccproxy/utils/token_counting.py` (new)
- Update all formatters to check model capabilities

## Benefits

### Minimal Integration
- ✅ Model metadata available in request flow
- ✅ Can log warnings about limits
- ✅ Foundation for future validation
- ✅ No breaking changes
- ⚠️  No enforcement (yet)

### Full Integration
- ✅ Token limit validation
- ✅ Capability checking
- ✅ Better error messages
- ✅ Prevents upstream errors
- ✅ Usage warnings in headers
- ⚠️  Requires token counting (adds latency)
- ⚠️  Breaking change if strict validation

## Recommendation

**Implement Minimal Integration first:**

1. It's low-risk and non-breaking
2. Makes model metadata available throughout request flow
3. Provides foundation for future enhancements
4. Can add validation incrementally

**Then add Full Integration features based on user feedback:**

1. Add opt-in token validation via config flag
2. Add capability warnings (non-blocking)
3. Eventually make validation opt-out

## Configuration

```toml
[plugins.codex]
dynamic_models_enabled = true

# Minimal Integration
model_metadata_enabled = true  # Default: true

# Full Integration
validate_token_limits = false   # Default: false (opt-in)
enforce_capabilities = false    # Default: false (opt-in)
warn_on_limits = true           # Default: true (warn but allow)
```

## Next Steps

1. Review this proposal
2. Decide: Minimal or Full integration?
3. Implement chosen approach
4. Test with real requests
5. Document new capabilities