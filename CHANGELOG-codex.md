# Changelog: Add OpenAI Codex Provider Support

## Added OpenAI Codex Provider with Full Proxy Support

### Overview
Implemented comprehensive support for OpenAI Codex CLI integration, enabling users to proxy requests through their OpenAI subscription via the ChatGPT backend API. This feature provides an alternative to the Claude provider while maintaining full compatibility with the existing proxy architecture. The implementation uses the OpenAI Responses API endpoint as documented at https://platform.openai.com/docs/api-reference/responses/get.

### Key Features

**Complete Codex API Proxy**
- Full reverse proxy to `https://chatgpt.com/backend-api/codex`
- Support for both `/codex/responses` and `/codex/{session_id}/responses` endpoints
- Compatible with Codex CLI 0.21.0 and authentication flow
- Implements OpenAI Responses API protocol

**OAuth PKCE Authentication Flow**
- Implements complete OpenAI OAuth 2.0 PKCE flow matching official Codex CLI
- Local callback server on port 1455 for authorization code exchange
- Token refresh and credential management with persistent storage
- Support for `~/.openai.toml` configuration file format

**Intelligent Request/Response Handling**
- Automatic detection and injection of Codex CLI instructions field
- Smart streaming behavior based on user's explicit `stream` parameter
- Session management with flexible session ID handling (auto-generated, persistent, header-forwarded)
- Request transformation preserving Codex CLI identity headers

**Advanced Configuration**
- Environment variable support: `CODEX__BASE_URL`
- Configurable via TOML: `[codex]` section in configuration files
- Debug logging with request/response capture capabilities
- Comprehensive error handling with proper HTTP status codes
- Enabled by default

### Technical Implementation

**New Components Added:**
- `ccproxy/auth/openai.py` - OAuth token management and credential storage
- `ccproxy/core/codex_transformers.py` - Request/response transformation for Codex format
- `ccproxy/api/routes/codex.py` - FastAPI routes for Codex endpoints
- `ccproxy/models/detection.py` - Codex CLI detection and header management
- `ccproxy/services/codex_detection_service.py` - Runtime detection of Codex CLI requests

**Enhanced Proxy Service:**
- Extended `ProxyService.handle_codex_request()` with full Codex support
- Intelligent streaming response conversion when user doesn't explicitly request streaming
- Comprehensive request/response logging for debugging
- Error handling with proper OpenAI-compatible error responses

### Streaming Behavior Fix

**Problem Resolved:** Fixed issue where requests without explicit `stream` field were incorrectly returning streaming responses.

**Solution Implemented:**
- When `"stream"` field is missing: Inject `"stream": true` for upstream (Codex requirement) but return JSON response to client
- When `"stream": true` explicitly set: Return streaming response to client  
- When `"stream": false` explicitly set: Return JSON response to client
- Smart response conversion: collects streaming data and converts to single JSON response when user didn't request streaming

### Usage Examples

**Basic Request (JSON Response):**
```bash
curl -X POST "http://127.0.0.1:8000/codex/responses" \
  -H "Content-Type: application/json" \
  -d '{
    "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Hello!"}]}],
    "model": "gpt-5",
    "store": false
  }'
```

**Streaming Request:**
```bash
curl -X POST "http://127.0.0.1:8000/codex/responses" \
  -H "Content-Type: application/json" \
  -d '{
    "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Hello!"}]}],
    "model": "gpt-5",
    "stream": true,
    "store": false
  }'
```

### Authentication Setup

**Environment Variables:**
```bash
export CODEX__BASE_URL="https://chatgpt.com/backend-api/codex"
```

**Configuration File (`~/.ccproxy.toml`):**
```toml
[codex]
base_url = "https://chatgpt.com/backend-api/codex"
```

### Compatibility

- Codex CLI: Full compatibility with `codex-cli 0.21.0`
- OpenAI OAuth: Complete PKCE flow implementation
- Session Management: Supports persistent and auto-generated sessions
- Model Support: All Codex-supported models (`gpt-5`, `gpt-4`, etc.)
- Streaming: Both streaming and non-streaming responses
- Error Handling: Proper HTTP status codes and OpenAI-compatible errors
- API Compliance: Follows OpenAI Responses API specification

### Files Modified/Added

**New Files:**
- `ccproxy/auth/openai.py` - OpenAI authentication management
- `ccproxy/core/codex_transformers.py` - Codex request/response transformation  
- `ccproxy/api/routes/codex.py` - Codex API endpoints
- `ccproxy/models/detection.py` - Codex detection models
- `ccproxy/services/codex_detection_service.py` - Codex CLI detection service

**Modified Files:**
- `ccproxy/services/proxy_service.py` - Added `handle_codex_request()` method
- `ccproxy/config/settings.py` - Added Codex configuration section
- `ccproxy/api/app.py` - Integrated Codex routes
- `ccproxy/api/routes/health.py` - Added Codex health checks

### Breaking Changes
None. This is a purely additive feature that doesn't affect existing Claude provider functionality.

### Migration Notes
For users wanting to use Codex provider:
1. Authenticate: Use existing OpenAI credentials or run Codex CLI login
2. Update endpoints: Change from `/v1/messages` to `/codex/responses`

This implementation provides a complete, production-ready OpenAI Codex proxy solution that maintains the same standards as the existing Claude provider while offering users choice in their AI provider preferences.
