# Authentication

## Overview

The Claude Code Proxy API supports optional bearer token authentication for securing access to the API endpoints.

## Bearer Token Authentication

### Configuration

Set the `BEARER_TOKEN` environment variable:

```bash
export BEARER_TOKEN="your-secret-token-here"
```

### Usage

Include the bearer token in your requests:

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-secret-token-here" \
  -d '{
    "model": "claude-3-5-sonnet-20241022",
    "messages": [
      {"role": "user", "content": "Hello, Claude!"}
    ]
  }'
```

## No Authentication

If no `BEARER_TOKEN` is set, the API will accept all requests without authentication.

## Security Considerations

- Always use HTTPS in production
- Keep your bearer token secret and secure
- Consider using environment variables or secure secret management systems
- Rotate tokens regularly