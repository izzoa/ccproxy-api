# CCProxy API Server

`ccproxy` is a local reverse proxy server that provides unified access to multiple AI providers through a plugin-based architecture. It supports Anthropic Claude and OpenAI Codex through dedicated provider plugins, allowing you to use your existing subscriptions and API keys.

## Architecture

CCProxy uses a modern plugin system that provides:

- **Provider Plugins**: Handle specific AI providers (Claude SDK, Claude API, Codex)
- **System Plugins**: Add functionality like logging, monitoring, and permissions
- **Unified API**: Consistent interface across all providers
- **Format Translation**: Seamless conversion between Anthropic and OpenAI formats

## Provider Modes

The server provides access through different provider plugins:

*   **Claude SDK (`/claude/`):** Routes requests through the local `claude-code-sdk`. This enables access to tools configured in your Claude environment and includes an integrated MCP (Model Context Protocol) server for permission management.
*   **Claude API (`/api/`):** Acts as a direct reverse proxy to `api.anthropic.com`, injecting the necessary authentication headers. This provides full access to the underlying API features and model settings.
*   **Codex (`/api/codex/`):** Provides access to OpenAI Codex models through OAuth2 authentication.

All modes support both Anthropic and OpenAI-compatible API formats for requests and responses, including streaming.

## Installation

```bash
# The official claude-code CLI is required for SDK mode
npm install -g @anthropic-ai/claude-code

# run it with uv
uvx ccproxy-api

# run it with pipx
pipx run ccproxy-api

# install with uv
uv tool install ccproxy-api

# Install ccproxy with pip
pipx install ccproxy-api

# Optional: Enable shell completion
eval "$(ccproxy --show-completion zsh)"  # For zsh
eval "$(ccproxy --show-completion bash)" # For bash
```


For dev version replace `ccproxy-api` with `git+https://github.com/caddyglow/ccproxy-api.git@dev`

## Authentication

Each provider plugin has its own authentication mechanism:

1.  **Claude SDK Plugin:**
    Relies on the authentication handled by the `claude-code-sdk`.
    ```bash
    claude /login
    ```

    For long-lived tokens:
    ```bash
    claude setup-token
    ```

2.  **Claude API Plugin:**
    Uses OAuth2 flow to obtain credentials for direct API access.
    ```bash
    ccproxy auth login claude-api
    ```

3.  **Codex Plugin:**
    Uses OpenAI OAuth2 flow for Codex access.
    ```bash  
    ccproxy auth login codex
    ```

You can check the status of these credentials with:
```bash
ccproxy auth status          # Check all providers
ccproxy auth status claude-api  # Check specific provider
```

A warning is shown on startup if no provider credentials are configured.

## Usage

### Running the Server

```bash
# Start the proxy server
ccproxy
```
The server will start on `http://127.0.0.1:8000` by default.

### Client Configuration

Point your existing tools and applications to the local proxy instance by setting the appropriate environment variables. A dummy API key is required by most client libraries but is not used by the proxy itself.

**For OpenAI-compatible clients:**
```bash
# Claude SDK plugin (routes at /claude)
export OPENAI_BASE_URL="http://localhost:8000/claude/v1"
# Claude API plugin (routes at /api)
export OPENAI_BASE_URL="http://localhost:8000/api/v1"
# Codex plugin (routes at /api/codex)
export OPENAI_BASE_URL="http://localhost:8000/api/codex/v1"

export OPENAI_API_KEY="dummy-key"
```

**For Anthropic-compatible clients:**
```bash
# Claude SDK plugin (routes at /claude)
export ANTHROPIC_BASE_URL="http://localhost:8000/claude"
# Claude API plugin (routes at /api)
export ANTHROPIC_BASE_URL="http://localhost:8000/api"

export ANTHROPIC_API_KEY="dummy-key"
```


## MCP Server Integration & Permission System

In SDK mode, CCProxy automatically configures an MCP (Model Context Protocol) server that provides permission checking tools for Claude Code. This enables interactive permission management for tool execution.

### Permission Management

**Starting the Permission Handler:**
```bash
# In a separate terminal, start the permission handler
ccproxy permission-handler

# Or with custom settings
ccproxy permission-handler --host 127.0.0.1 --port 8000
```

The permission handler provides:
- **Real-time Permission Requests**: Streams permission requests via Server-Sent Events (SSE)
- **Interactive Approval/Denial**: Command-line interface for managing tool permissions
- **Automatic MCP Integration**: Works seamlessly with Claude Code SDK tools

**Working Directory Control:**
Control which project the Claude SDK API can access using the `--cwd` flag:
```bash
# Set working directory for Claude SDK
ccproxy --claude-code-options-cwd /path/to/your/project

# Example with permission bypass and formatted output
ccproxy --claude-code-options-cwd /tmp/tmp.AZyCo5a42N \
        --claude-code-options-permission-mode bypassPermissions \
        --claude-sdk-message-mode formatted

# Alternative: Change to project directory and start ccproxy
cd /path/to/your/project
ccproxy
```

### Claude SDK Message Formatting

CCProxy supports flexible message formatting through the `sdk_message_mode` configuration:

- **`forward`** (default): Preserves original Claude SDK content blocks with full metadata
- **`formatted`**: Converts content to XML tags with pretty-printed JSON data
- **`ignore`**: Filters out Claude SDK-specific content entirely

Configure via environment variables:
```bash
# Use formatted XML output
CLAUDE__SDK_MESSAGE_MODE=formatted ccproxy

# Use compact formatting without pretty-printing
CLAUDE__PRETTY_FORMAT=false ccproxy
```

## Using with Aider

CCProxy works seamlessly with Aider and other AI coding assistants:

### Anthropic Mode
```bash
export ANTHROPIC_API_KEY=dummy
# Use Claude API plugin
export ANTHROPIC_BASE_URL=http://127.0.0.1:8000/api
aider --model claude-sonnet-4-20250514
```

### OpenAI Mode with Model Mapping

If your tool only supports OpenAI settings, ccproxy automatically maps OpenAI models to Claude:

```bash
export OPENAI_API_KEY=dummy
# Use Claude API plugin
export OPENAI_BASE_URL=http://127.0.0.1:8000/api/v1
aider --model o3-mini

# Or use Codex plugin for OpenAI models  
export OPENAI_BASE_URL=http://127.0.0.1:8000/api/codex/v1
aider --model gpt-4-turbo
```

### Claude SDK Mode (With Tools)

For accessing Claude with local development tools:

```bash
export OPENAI_API_KEY=dummy
export OPENAI_BASE_URL=http://127.0.0.1:8000/claude/v1
aider --model claude-3-5-sonnet-20241022
```

### `curl` Example

```bash
# Claude SDK plugin
curl -X POST http://localhost:8000/claude/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-3-5-sonnet-20241022",
    "messages": [{"role": "user", "content": "Hello!"}],
    "max_tokens": 100
  }'

# Claude API plugin
curl -X POST http://localhost:8000/api/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-3-5-sonnet-20241022",
    "messages": [{"role": "user", "content": "Hello!"}],
    "max_tokens": 100
  }'
```
More examples are available in the `examples/` directory.

## Endpoints

The proxy exposes endpoints through different provider plugins:

| Plugin | URL Prefix | Description | Use Case |
|--------|------------|-------------|----------|
| **Claude SDK** | `/claude/` | Uses `claude-code-sdk` with its configured tools. | Accessing Claude with local tools. |
| **Claude API** | `/api/` | Direct proxy with header injection. | Full API control, direct access. |
| **Codex** | `/api/codex/` | OpenAI Codex access via OAuth2. | OpenAI Codex models. |

*   **Anthropic Format:**
    *   `POST /claude/v1/messages` (Claude SDK)
    *   `POST /api/v1/messages` (Claude API)
*   **OpenAI Format:**
    *   `POST /claude/v1/chat/completions` (Claude SDK)
    *   `POST /api/v1/chat/completions` (Claude API)
    *   `POST /api/codex/v1/chat/completions` (Codex)
*   **Plugin Management:**
    *   `GET /api/plugins` - List all plugins
    *   `GET /api/plugins/{name}/health` - Plugin health check
*   **Authentication:**
    *   `GET /oauth/callback` - OAuth callback handler
*   **Utility:**
    *   `GET /health` - Server health check
    *   `GET /claude/models`, `GET /api/models` - Available models
    *   `GET /claude/status`, `GET /api/status` - Provider status
*   **MCP & Permissions (when enabled):**
    *   `POST /mcp/permission/check` - MCP permission checking
    *   `GET /permissions/stream` - SSE stream for permission requests
    *   `GET /permissions/{id}` - Get permission request details
    *   `POST /permissions/{id}/respond` - Respond to permission request
*   **Observability (Optional):**
    *   `GET /metrics` - Prometheus metrics
    *   `GET /logs/status`, `GET /logs/query` - Log querying
    *   `GET /dashboard` - Real-time dashboard

## Supported Models

CCProxy supports recent Claude models including Opus, Sonnet, and Haiku variants. The specific models available to you will depend on your Claude account and the features enabled for your subscription.

 * `claude-opus-4-20250514`
 * `claude-sonnet-4-20250514`
 * `claude-3-7-sonnet-20250219`
 * `claude-3-5-sonnet-20241022`
 * `claude-3-5-sonnet-20240620`

## Configuration

Settings can be configured through (in order of precedence):
1. Command-line arguments
2. Environment variables
3. `.env` file
4. TOML configuration files (`.ccproxy.toml`, `ccproxy.toml`, or `~/.config/ccproxy/config.toml`)
5. Default values

For complex configurations, you can use a nested syntax for environment variables with `__` as a delimiter:

```bash
# Server settings
SERVER__HOST=0.0.0.0
SERVER__PORT=8080
# etc.
```

## Securing the Proxy (Optional)

You can enable token authentication for the proxy. This supports multiple header formats (`x-api-key` for Anthropic, `Authorization: Bearer` for OpenAI) for compatibility with standard client libraries.

**1. Generate a Token:**
```bash
ccproxy config generate-token
# Output: SECURITY__AUTH_TOKEN=abc123xyz789...
```

**2. Configure the Token:**
```bash
# Set environment variable
export SECURITY__AUTH_TOKEN=abc123xyz789...

# Or add to .env file
echo "SECURITY__AUTH_TOKEN=abc123xyz789..." >> .env
```

**3. Use in Requests:**
When authentication is enabled, include the token in your API requests.
```bash
# Anthropic Format (x-api-key)
curl -H "x-api-key: your-token" ...

# OpenAI/Bearer Format
curl -H "Authorization: Bearer your-token" ...
```

## Observability

`ccproxy` includes an optional but powerful observability suite for monitoring and analytics. When enabled, it provides:

*   **Prometheus Metrics:** A `/metrics` endpoint for real-time operational monitoring.
*   **Access Log Storage:** Detailed request logs, including token usage and costs, are stored in a local DuckDB database.
*   **Analytics API:** Endpoints to query and analyze historical usage data.
*   **Real-time Dashboard:** A live web interface at `/dashboard` to visualize metrics and request streams.

These features are disabled by default and can be enabled via configuration. For a complete guide on setting up and using these features, see the [Observability Documentation](observability.md).

## Troubleshooting

### Common Issues

1.  **Authentication Error:** Ensure you're using the correct plugin endpoint for your authentication method.
2.  **Plugin Credentials Expired:**
    - Claude API: Run `ccproxy auth login claude-api`
    - Codex: Run `ccproxy auth login codex`  
    - Claude SDK: Run `claude /login`
3.  **Missing API Auth Token:** If you've enabled security, include the token in your request headers.
4.  **Port Already in Use:** Start the server on a different port: `ccproxy --port 8001`.
5.  **Model Not Available:** Check that your subscription includes the requested model for the specific provider.
6.  **Plugin Not Loading:** Check logs for plugin initialization errors: `ccproxy --log-level debug`

## Contributing

Please see [CONTRIBUTING.md](CONTRIBUTING.md) for details.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Documentation

- **[Online Documentation](https://caddyglow.github.io/ccproxy-api)**
- **[Plugin System Guide](PLUGIN_SYSTEM_DOCUMENTATION.md)**
- **[OAuth Architecture](OAUTH_PLUGIN_ARCHITECTURE.md)**

## Support

- Issues: [GitHub Issues](https://github.com/CaddyGlow/ccproxy-api/issues)
- Documentation: [Project Documentation](https://caddyglow.github.io/ccproxy-api)

## Acknowledgments

- [Anthropic](https://anthropic.com) for Claude and the Claude Code SDK
- The open-source community
