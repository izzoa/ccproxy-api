# API Reference

Claude Code Proxy provides multiple endpoint modes for different use cases.

## Endpoint Modes

### Claude Code Mode (Default)
- **Base URL**: `http://localhost:8000/` or `http://localhost:8000/cc/`
- **Method**: Uses the official claude-code-sdk
- **Limitations**: Cannot use ToolCall, limited model settings control
- **Advantages**: Access to all Claude Code tools and features

### API Mode (Direct Proxy)
- **Base URL**: `http://localhost:8000/api/`
- **Method**: Direct reverse proxy to api.anthropic.com
- **Features**: Full API access, all model settings available
- **Authentication**: Injects OAuth headers automatically

### OpenAI Codex Mode (Experimental)
- **Base URL**: `http://localhost:8000/codex/`
- **Method**: Direct reverse proxy to chatgpt.com/backend-api/codex
- **Features**: ChatGPT Plus Response API access
- **Requirements**: Active ChatGPT Plus subscription
- **Documentation**: [OpenAI Response API](https://platform.openai.com/docs/api-reference/responses)

## Supported Endpoints

### Anthropic Format
```
POST /v1/messages         # Claude Code mode (default)
POST /api/v1/messages     # API mode (direct proxy)
POST /cc/v1/messages      # Claude Code mode (explicit)
```

### OpenAI Compatibility Layer
```
POST /v1/chat/completions           # Claude Code mode (default)
POST /api/v1/chat/completions       # API mode (direct proxy)
POST /cc/v1/chat/completions        # Claude Code mode (explicit)
POST /sdk/v1/chat/completions       # Claude SDK mode (explicit)
```

### OpenAI Codex Response API
```
POST /codex/responses                    # Auto-generated session
POST /codex/{session_id}/responses       # Persistent session
```

### Utility Endpoints
```
GET /health              # Health check
GET /v1/models           # List available models
GET /sdk/models          # List models (SDK mode)
GET /api/models          # List models (API mode)
```

## Available Models

### Claude Models
Models available depend on your Claude subscription:

- `claude-opus-4-20250514` - Claude 4 Opus (most capable)
- `claude-sonnet-4-20250514` - Claude 4 Sonnet (latest)
- `claude-3-7-sonnet-20250219` - Claude 3.7 Sonnet
- `claude-3-5-sonnet-20241022` - Claude 3.5 Sonnet
- `claude-3-5-sonnet-20240620` - Claude 3.5 Sonnet (legacy)

### OpenAI Codex Models
Models available with ChatGPT Plus subscription:

- `gpt-4` - GPT-4 (ChatGPT Plus version)
- `gpt-4-turbo` - GPT-4 Turbo (faster variant)
- `gpt-3.5-turbo` - GPT-3.5 Turbo (base model)

## Request Format

### Anthropic Format
```json
{
  "model": "claude-3-5-sonnet-20241022",
  "messages": [{"role": "user", "content": "Hello"}],
  "max_tokens": 1000
}
```

### OpenAI Format
```json
{
  "model": "claude-3-5-sonnet-20241022",
  "messages": [{"role": "user", "content": "Hello"}],
  "max_tokens": 1000,
  "temperature": 0.7
}
```

### Codex Response API Format
```json
{
  "model": "gpt-4",
  "messages": [
    {"role": "user", "content": "Hello, can you help me?"}
  ],
  "temperature": 0.7,
  "max_tokens": 1000,
  "stream": false
}
```

**Note**: The Codex instruction prompt is automatically injected into all requests.

## Authentication

### Claude Endpoints
- **OAuth Users**: No API key needed, uses Claude subscription
- **API Key Users**: Include `x-api-key` header or `Authorization: Bearer` header

### Codex Endpoints
- **ChatGPT Plus Required**: Active subscription needed
- **OAuth2 Authentication**: Uses credentials from `$HOME/.codex/auth.json`
- **Auto-renewal**: Tokens refreshed automatically when expired

## Streaming

Both modes support streaming responses:
```json
{
  "model": "claude-3-5-sonnet-20241022",
  "messages": [{"role": "user", "content": "Tell me a story"}],
  "stream": true
}
```

## Mode Selection Guide

| Use Case | Recommended Mode | Endpoint |
|----------|------------------|----------|
| Need Claude Code tools | Claude Code mode | `/v1/messages` |
| Need full API control | API mode | `/api/v1/messages` |
| Using OpenAI SDK with Claude | Either mode | `/v1/chat/completions` or `/api/v1/chat/completions` |
| Direct API access | API mode | `/api/v1/messages` |
| ChatGPT Plus access | Codex mode | `/codex/responses` |
| Session-based conversations | Codex mode | `/codex/{session_id}/responses` |
