# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

#### Codex Provider Enhancements
- **Observability & Metrics**: Full observability support for Codex requests with Prometheus metrics and structured logging
  - Wrapped `handle_codex_request` with request context and timed operations
  - Added `StreamingResponseWithLogging` for consistent access logging
  - Prometheus metrics now include `service_type="codex"` labels
  - Token usage and cost tracking for all Codex requests

- **API Feature Parity**: Complete OpenAI Chat Completions API compatibility
  - New `/codex/chat/completions` and `/codex/{session_id}/chat/completions` endpoints
  - Full tool/function calling support via ResponseAdapter
  - Reasoning content handling for capable models (o1, o3, etc.)
  - Response format and JSON schema support
  - Dynamic model capability detection via ModelInfoService
  - Parameter propagation for temperature, top_p, max_tokens, etc.
  - Image content handling in messages

- **Configuration & CLI**: Enhanced configuration and management tools
  - New `system_prompt_injection_mode` setting (override/append/disabled)
  - `enable_dynamic_model_info` for automatic capability detection  
  - `max_output_tokens_fallback` for default token limits
  - `propagate_unsupported_params` for parameter pass-through
  - New `ccproxy codex` CLI commands for info, cache management, and testing
  - CLI configuration display now includes Codex settings group

- **Testing**: Comprehensive test coverage
  - Added `TestCodexProxyObservability` test suite
  - Created `test_response_adapter.py` with full adapter coverage
  - Built `test_codex_instruction_modes.py` for injection mode testing
  - Test coverage for streaming, tool calls, reasoning, and error handling

### Changed

- **Codex Request Transformer**: Refactored instruction injection logic
  - Supports configurable injection modes (override, append, disabled)
  - Model placeholder substitution in instruction templates
  - Better handling of multiple system messages
  - Graceful fallback for empty/missing templates

- **Response Adapter**: Enhanced OpenAI compatibility layer
  - Async methods with dynamic model info integration
  - Improved streaming conversion with tool calls and reasoning
  - Better model mapping between OpenAI and Response API formats
  - Comprehensive error handling during stream processing

- **Docker Support**: Updated entrypoint script
  - Creates `.codex` directory for credential storage
  - Ensures proper permissions for Codex auth files

### Fixed

- Context metadata properly updated for non-streaming Codex responses
- Streaming responses now consistently use shared logging infrastructure
- Tool call arguments handling for malformed JSON
- Mixed content type handling (string vs list content)
- Session management for persistent conversations

### Documentation

- Updated README with comprehensive Codex feature documentation
- Removed outdated limitations, added new capabilities
- Added CLI command examples for Codex management
- Enhanced authentication and configuration instructions

## Migration Guide

### For Codex Users

1. **Update Configuration**: New settings are available for fine-tuning Codex behavior:
   ```bash
   export CODEX__SYSTEM_PROMPT_INJECTION_MODE=append  # Control instruction injection
   export CODEX__ENABLE_DYNAMIC_MODEL_INFO=true       # Enable model capability detection
   ```

2. **Use New Endpoints**: The `/codex/chat/completions` endpoint provides better OpenAI compatibility:
   ```bash
   # Old endpoint (still supported)
   curl -X POST http://localhost:8000/codex/responses
   
   # New endpoint (recommended)
   curl -X POST http://localhost:8000/codex/chat/completions
   ```

3. **Tool Calling**: Now fully supported in chat completions format:
   ```json
   {
     "model": "gpt-4o",
     "messages": [...],
     "tools": [...],
     "tool_choice": "auto"
   }
   ```

4. **CLI Management**: Use new commands to manage Codex:
   ```bash
   ccproxy codex info         # View configuration
   ccproxy codex cache --list # Manage detection cache
   ccproxy codex test         # Test connectivity
   ```

### Breaking Changes

- None. All existing Codex functionality remains backward compatible.

### Deprecations

- The limitation notes about tool calling and parameter support in README have been removed as these features are now fully supported.
