# CCProxy API Server

`ccproxy` is a local reverse proxy server that provides unified access to multiple AI providers through a single interface. It supports both Anthropic Claude and OpenAI Codex backends, allowing you to use your existing subscriptions without separate API key billing.

> **About this fork**
>
> This README documents the Codex parity fork hosted at [https://github.com/izzoa/ccproxy-api](https://github.com/izzoa/ccproxy-api). The fork layers additional OpenAI Response API features, dynamic model discovery, richer observability, and CLI management tools on top of the upstream project.

## Fork Highlights

- **Codex Parity:** Tool/function calling, sampling parameters, reasoning deltas, and usage stats now round-trip between OpenAI Chat Completions clients and the ChatGPT Response API.
- **Dynamic Model Intelligence:** Optional live discovery of model limits via the shared model-info service, plus conservative fallbacks when network data is unavailable.
- **Configurable Instruction Modes:** `override`, `append`, and `disabled` modes let you balance Codex requirements with custom system prompts.
- **Enhanced CLI:** `ccproxy codex info`, `ccproxy codex set`, and `ccproxy codex cache` expose Codex configuration, detection cache introspection, and quick toggles without manual TOML edits.
- **Observability Alignment:** Codex traffic now emits the same request context, Prometheus metrics, and streaming logs as Claude traffic.

## Supported Providers

### Anthropic Claude

Access Claude via your Claude Max subscription at `api.anthropic.com/v1/messages`.

The server provides two primary modes of operation:

- **SDK Mode (`/sdk`):** Routes requests through the local `claude-code-sdk`. This enables access to tools configured in your Claude environment and includes an integrated MCP (Model Context Protocol) server for permission management.
- **API Mode (`/api`):** Acts as a direct reverse proxy, injecting the necessary authentication headers. This provides full access to the underlying API features and model settings.

### OpenAI Codex Response API

Access OpenAI's [Response API](https://platform.openai.com/docs/api-reference/responses) via your ChatGPT Plus subscription. This provides programmatic access to ChatGPT models through the `chatgpt.com/backend-api/codex` endpoint.

- **Response API (`/codex/responses`):** Direct reverse proxy to ChatGPT backend for conversation responses
- **Chat Completions (`/codex/chat/completions`):** OpenAI-compatible endpoint with tool calling and advanced features
- **Session Management:** Supports both auto-generated and persistent session IDs for conversation continuity
- **OpenAI OAuth:** Uses the same OAuth2 PKCE authentication flow as the official Codex CLI
- **ChatGPT Plus Required:** Requires an active ChatGPT Plus subscription for API access
- **Configurable Instruction Injection:** Control how system prompts are handled (override, append, or disabled)
- **Dynamic Model Information:** Automatically fetches model capabilities and token limits
- **Enhanced Observability:** Full metrics and logging support with Prometheus integration

The server includes a translation layer to support both Anthropic and OpenAI-compatible API formats for requests and responses, including streaming.

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

For bleeding-edge changes install directly from this fork:

```bash
pipx install "git+https://github.com/izzoa/ccproxy-api.git@main"
```

## Docker Compose Setup

CCProxy can be run using Docker Compose for containerized deployment. This approach provides isolation, easy management, and optional monitoring stack integration.

### Prerequisites

Before running with Docker Compose, ensure you have the required authentication credentials:

1. **Claude Authentication**: Set up Claude CLI credentials on your host system:
   ```bash
   # Install Claude CLI (if not already installed)
   npm install -g @anthropic-ai/claude-code
   
   # Authenticate with Claude
   claude auth login
   # OR for long-lived tokens:
   claude setup-token
   ```

2. **OpenAI Codex Authentication** (Optional, for Codex provider):
   ```bash
   # Install Codex CLI (if not already installed)
   npm install -g @anthropic-ai/codex
   
   # Authenticate with OpenAI
   codex auth login
   # OR use CCProxy's authentication:
   ccproxy auth login-openai
   ```

### Basic Setup

1. **Clone the repository and navigate to the project directory:**
   ```bash
   git clone https://github.com/izzoa/ccproxy-api.git
   cd ccproxy-api
   ```

2. **Create environment configuration:**
   ```bash
   # Copy example environment file
   cp .env.example .env
   
   # Edit .env file with your preferred settings
   # Key settings:
   # SERVER__PORT=8000          # Port for the proxy server
   # PUID=1000                  # User ID (matches your host user)
   # PGID=1000                  # Group ID (matches your host group)
   ```

3. **Start the service:**
   ```bash
   # Start CCProxy
   docker-compose up -d
   
   # View logs
   docker-compose logs -f claude-code-proxy
   
   # Check service health
   curl http://localhost:8000/health
   ```

### Environment Configuration

The Docker Compose setup supports these key environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `SERVER__PORT` | `8000` | Port for the proxy server |
| `PUID` | `1000` | User ID for the claude user inside container |
| `PGID` | `1000` | Group ID for the claude group inside container |
| `CLAUDE_HOME` | `/data/home` | Claude user home directory in container |
| `SECURITY__AUTH_TOKEN` | (unset) | Optional token for securing proxy access |

### Volume Mounts

The Docker Compose configuration automatically mounts essential credential files:

```yaml
volumes:
  # Claude CLI credentials (required for Claude access)
  - ~/.config/claude:/data/home/.config/claude:ro
  
  # OpenAI Codex credentials (required for Codex access)  
  - ~/.codex/auth.json:/data/home/.codex/auth.json:ro
```

**Important**: These files must exist on your host system before starting the container. Run the authentication commands mentioned in Prerequisites to create them.

### Monitoring Stack (Optional)

CCProxy includes an optional monitoring stack with Grafana and Victoria Metrics:

1. **Start monitoring services:**
   ```bash
   # Start monitoring stack
   docker-compose -f docker-compose.monitoring.yml up -d
   
   # Start both main service and monitoring
   docker-compose -f docker-compose.yml -f docker-compose.monitoring.yml up -d
   ```

2. **Access monitoring interfaces:**
   - **Grafana Dashboard**: http://localhost:3000 (admin/admin)
   - **Victoria Metrics**: http://localhost:8428
   - **CCProxy Metrics**: http://localhost:8000/metrics (when observability enabled)

3. **Enable observability in CCProxy:**
   ```bash
   # Add to .env file
   echo "OBSERVABILITY__ENABLED=true" >> .env
   echo "OBSERVABILITY__METRICS_ENABLED=true" >> .env
   
   # Restart CCProxy
   docker-compose restart claude-code-proxy
   ```

### Docker Commands Reference

```bash
# Start services
docker-compose up -d

# View logs
docker-compose logs -f claude-code-proxy

# Restart service
docker-compose restart claude-code-proxy

# Stop services
docker-compose down

# Rebuild and start (after code changes)
docker-compose up -d --build

# View service status
docker-compose ps

# Execute commands in running container
docker-compose exec claude-code-proxy ccproxy auth status

# Clean up everything (including volumes)
docker-compose down -v
```

### Troubleshooting Docker Setup

**1. Credential Mount Issues:**
```bash
# Check if credential files exist
ls -la ~/.config/claude/.credentials.json
ls -la ~/.codex/auth.json

# If missing, authenticate first:
claude auth login    # For Claude
codex auth login     # For Codex (optional)
```

**2. Permission Issues:**
```bash
# Check PUID/PGID match your user
id
# Update .env file with correct values:
# PUID=1001  # Your user ID
# PGID=1001  # Your group ID
```

**3. Port Conflicts:**
```bash
# Change port in .env file
echo "SERVER__PORT=8001" >> .env
docker-compose up -d
```

**4. Container Build Issues:**
```bash
# Force rebuild
docker-compose build --no-cache claude-code-proxy

# Clean Docker system
docker system prune -f
```

**5. Authentication Status Check:**
```bash
# Check authentication inside container
docker-compose exec claude-code-proxy ccproxy auth status
```

## Authentication

The proxy uses different authentication mechanisms depending on the provider and mode.

### Claude Authentication

1.  **Claude CLI (`sdk` mode):**
    This mode relies on the authentication handled by the `claude-code-sdk`.

    ```bash
    claude /login
    ```

    It's also possible now to get a long live token to avoid renewing issues
    using

    ```bash
    claude setup-token
    ```

2.  **ccproxy (`api` mode):**
    This mode uses its own OAuth2 flow to obtain credentials for direct API access.

    ```bash
    ccproxy auth login
    ```

    If you are already connected with Claude CLI the credentials should be found automatically

### OpenAI Codex Authentication (Experimental)

The Codex Response API requires ChatGPT Plus subscription and OAuth2 authentication:

```bash
# Inspect active Codex configuration and model capabilities
ccproxy codex info

# Update Codex toggles without editing the TOML config manually
ccproxy codex set \
  --enable-dynamic-model-info \
  --max-output-tokens-fallback 8192 \
  --system-prompt-injection-mode append

# Manage Codex detection cache
ccproxy codex cache           # Show cached headers/instructions
ccproxy codex cache --raw     # Raw JSON dump
ccproxy codex cache --clear   # Remove cached data

# Connectivity smoke test
ccproxy codex test

# Authentication options:

# Option 1: Reuse existing Codex CLI credentials (preferred)
# CCProxy automatically reads $HOME/.codex/auth.json and refreshes tokens.

# Option 2: Login via CCProxy CLI (opens browser)
ccproxy auth login-openai

# Option 3: Use the official Codex CLI and let CCProxy reuse the token
codex auth login

# Check authentication status for all providers
ccproxy auth status
```

**Important Notes:**

- Credentials are stored in `$HOME/.codex/auth.json`.
- CCProxy reuses existing Codex CLI credentials when available and refreshes them automatically when possible.
- If no valid credentials exist, authenticate with either `ccproxy auth login-openai` or the official Codex CLI.
- Environment variables (e.g., `CODEX__SYSTEM_PROMPT_INJECTION_MODE`) remain supported for headless deployments, but `ccproxy codex set` is the recommended workflow.

### Authentication Status

You can check the status of all credentials with:

```bash
ccproxy auth status       # All providers
ccproxy auth validate     # Claude only
ccproxy auth info         # Claude only
```

Warning is shown on startup if no credentials are setup.

## Usage

### Running the Server

```bash
# Start the proxy server
ccproxy
```

The server will start on `http://127.0.0.1:8000` by default.

### Client Configuration

Point your existing tools and applications to the local proxy instance by setting the appropriate environment variables. A dummy API key is required by most client libraries but is not used by the proxy itself.

**For Claude (OpenAI-compatible clients):**

```bash
# For SDK mode
export OPENAI_BASE_URL="http://localhost:8000/sdk/v1"
# For API mode
export OPENAI_BASE_URL="http://localhost:8000/api/v1"

export OPENAI_API_KEY="dummy-key"
```

**For Claude (Anthropic-compatible clients):**

```bash
# For SDK mode
export ANTHROPIC_BASE_URL="http://localhost:8000/sdk"
# For API mode
export ANTHROPIC_BASE_URL="http://localhost:8000/api"

export ANTHROPIC_API_KEY="dummy-key"
```

**For OpenAI Codex Response API:**

```bash
# Create a new conversation response (auto-generated session)
curl -X POST http://localhost:8000/codex/responses \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-5",
    "messages": [
      {"role": "user", "content": "Hello, can you help me with Python?"}
    ]
  }'

# Continue conversation with persistent session ID
curl -X POST http://localhost:8000/codex/my_session_123/responses \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-5",
    "messages": [
      {"role": "user", "content": "Show me an example of async/await"}
    ]
  }'

# Stream responses (SSE format)
curl -X POST http://localhost:8000/codex/responses \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-5",
    "messages": [{"role": "user", "content": "Explain quantum computing"}],
    "stream": true
  }'
```

**For OpenAI-compatible clients using Codex:**

```yaml
# Example aichat configuration (~/.config/aichat/config.yaml)
clients:
  - type: claude
    api_base: http://127.0.0.1:8000/codex

# Usage
aichat --model openai:gpt-5 "hello"
```

**Codex Features & Capabilities:**

- **Tool/Function Calling:** Full support for OpenAI-style tool and function calling
- **Parameter Support:** Propagation of temperature, top_p, and other OpenAI parameters (where supported by backend)
- **Dynamic Model Info:** Automatic detection of model capabilities and token limits
- **Reasoning Mode:** Enhanced handling of reasoning content for capable models
- **Configurable System Prompts:** Control instruction injection (override, append, or disabled)
- **Session Persistence:** Full support for maintaining conversation context across requests

**Note:** Adjust instruction injection with `ccproxy codex set --system-prompt-injection-mode {override|append|disabled}` (or the corresponding `CODEX__SYSTEM_PROMPT_INJECTION_MODE` environment variable for automation).

**Codex CLI quick reference:**

```bash
ccproxy codex info            # Inspect current configuration, detection cache, and model limits
ccproxy codex set ...         # Persist configuration changes to the active ccproxy TOML file
ccproxy codex cache           # Display cached detection data (use --raw or --clear as needed)
ccproxy codex test            # Run a connectivity smoke test against the ChatGPT backend
```

### Codex Response API Details

#### Session Management

The Codex Response API supports flexible session management for conversation continuity:

- **Auto-generated sessions**: `POST /codex/responses` - Creates a new session ID for each request
- **Persistent sessions**: `POST /codex/{session_id}/responses` - Maintains conversation context across requests
- **Header forwarding**: Optional `session_id` header for custom session tracking

#### Instruction Prompt Injection

**Important:** CCProxy automatically injects the Codex instruction prompt into conversations to match the ChatGPT backend expectations. You can tune this behaviour via `ccproxy codex set --system-prompt-injection-mode {override|append|disabled}`, but disabling injection may cause degraded or rejected responses:

- The instruction prompt is prepended (or appended) to your messages by default.
- This consumes additional tokens in each request—plan allowances accordingly.
- Use the `append` mode to keep your own system prompt while preserving Codex requirements, or `disabled` only for advanced experiments.

#### Model Differences

The Response API models differ from standard OpenAI API models:

- Uses the ChatGPT Response API catalog (e.g., `gpt-5`, `gpt-4o`, `gpt-4o-mini`, `o1`, `o3-mini`).
- Model behavior matches the ChatGPT web interface, including reasoning trace formatting.
- Token limits and pricing follow ChatGPT Plus subscription entitlements—consult `ccproxy codex info` for live limits.
- See [OpenAI Response API Documentation](https://platform.openai.com/docs/api-reference/responses) for the authoritative specification.

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

## Claude SDK Pool Mode

CCProxy supports connection pooling for Claude Code SDK clients to improve request performance by maintaining a pool of pre-initialized Claude instances.

### Benefits

- **Reduced Latency**: Eliminates Claude Code startup overhead on each request
- **Improved Performance**: Reuses established connections for faster response times
- **Resource Efficiency**: Maintains a configurable pool size to balance performance and resource usage

### Usage

Pool mode is disabled by default and can be enabled using the CLI flag:

```bash
# Enable pool mode with default settings
ccproxy --sdk-enable-pool

# Configure pool size (default: 3)
ccproxy --sdk-enable-pool --sdk-pool-size 5
```

### Limitations

- **No Dynamic Options**: Pool instances cannot change Claude options (max_tokens, model, etc.) after initialization
- **Shared Configuration**: All requests using the pool must use identical Claude configuration
- **Memory Usage**: Each pool instance consumes additional memory

Pool mode is most effective for high-frequency requests with consistent configuration requirements.

## Using with Aider

CCProxy works seamlessly with Aider and other AI coding assistants:

### Anthropic Mode

```bash
export ANTHROPIC_API_KEY=dummy
export ANTHROPIC_BASE_URL=http://127.0.0.1:8000/api
aider --model claude-sonnet-4-20250514
```

### OpenAI Mode with Model Mapping

If your tool only supports OpenAI settings, ccproxy automatically maps OpenAI models to Claude:

```bash
export OPENAI_API_KEY=dummy
export OPENAI_BASE_URL=http://127.0.0.1:8000/api/v1
aider --model o3-mini
```

### API Mode (Direct Proxy)

For minimal interference and direct API access:

```bash
export OPENAI_API_KEY=dummy
export OPENAI_BASE_URL=http://127.0.0.1:8000/api/v1
aider --model o3-mini
```

### Using with OpenAI Codex

For tools that support custom API bases, you can use the Codex provider. The Codex proxy now mirrors the ChatGPT Response API feature set—including tool/function calling, sampling parameters, and dynamic model limits—while using your ChatGPT Plus subscription.

**Example with aichat:**

```yaml
# ~/.config/aichat/config.yaml
clients:
  - type: claude
    api_base: http://127.0.0.1:8000/codex
```

```bash
# Usage with confirmed working model
aichat --model openai:gpt-5 "hello"
```

**Codex Notes:**

- A ChatGPT Plus subscription is still required; CCProxy authenticates with OAuth on first use.
- Available models track the ChatGPT Response API. Run `ccproxy codex info` to inspect support or enable dynamic discovery with `ccproxy codex set --enable-dynamic-model-info`.
- The backend only returns a single choice (`n=1`) and does not expose logprobs/top_logprobs today.
- Reasoning-capable models emit `<thinking>…</thinking>` segments; strip them if your client cannot display reasoning traces.

### `curl` Example

```bash
# SDK mode
curl -X POST http://localhost:8000/sdk/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-3-5-sonnet-20241022",
    "messages": [{"role": "user", "content": "Hello!"}],
    "max_tokens": 100
  }'

# API mode
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

The proxy exposes endpoints under multiple prefixes for different providers and modes.

### Claude Endpoints

| Mode    | URL Prefix | Description                                       | Use Case                           |
| ------- | ---------- | ------------------------------------------------- | ---------------------------------- |
| **SDK** | `/sdk/`    | Uses `claude-code-sdk` with its configured tools. | Accessing Claude with local tools. |
| **API** | `/api/`    | Direct proxy with header injection.               | Full API control, direct access.   |

- **Anthropic Format:**
  - `POST /sdk/v1/messages`
  - `POST /api/v1/messages`
- **OpenAI-Compatible Format:**
  - `POST /sdk/v1/chat/completions`
  - `POST /api/v1/chat/completions`

### OpenAI Codex Endpoints

- **Response API:**
  - `POST /codex/responses` - Create response with auto-generated session
  - `POST /codex/{session_id}/responses` - Create response with persistent session
  - `POST /codex/chat/completions` - OpenAI-compatible chat completions endpoint
  - `POST /codex/v1/chat/completions` - Alternative OpenAI-compatible endpoint
  - Supports streaming via SSE when `stream: true` is set
  - See [Response API docs](https://platform.openai.com/docs/api-reference/responses)

**Codex Chat Completions Features:**

- **Tool/Function Calling**: Full support for OpenAI-style tool and function calling
- **Parameter Support**: Most OpenAI parameters (temperature, top_p, max_tokens, etc.) are now supported
- **Extended Model Support**: Dynamic model detection and expanded model compatibility
- **Configurable System Prompts**: Control how system messages are handled via injection modes
- **Reasoning Mode**: Enhanced handling of reasoning content for capable models
- **Session Management**: Both auto-generated and persistent sessions via `/codex/{session_id}/chat/completions`
- **ChatGPT Plus Required**: Requires active ChatGPT Plus subscription for access

**Note**: Both `/codex/responses` and `/codex/chat/completions` endpoints now support full tool calling and most OpenAI parameters. The chat completions endpoint provides better OpenAI API compatibility.

### Utility Endpoints

- **Health & Status:**
  - `GET /health`
  - `GET /sdk/models`, `GET /api/models`
  - `GET /sdk/status`, `GET /api/status`
- **Authentication:**
  - `GET /oauth/callback` - OAuth callback for both Claude and OpenAI
- **MCP & Permissions:**
  - `POST /mcp/permission/check` - MCP permission checking endpoint
  - `GET /permissions/stream` - SSE stream for permission requests
  - `GET /permissions/{id}` - Get permission request details
  - `POST /permissions/{id}/respond` - Respond to permission request
- **Observability (Optional):**
  - `GET /metrics`
  - `GET /logs/status`, `GET /logs/query`
  - `GET /dashboard`

## Supported Models

CCProxy supports recent Claude models including Opus, Sonnet, and Haiku variants. The specific models available to you will depend on your Claude account and the features enabled for your subscription.

- `claude-opus-4-20250514`
- `claude-sonnet-4-20250514`
- `claude-3-7-sonnet-20250219`
- `claude-3-5-sonnet-20241022`
- `claude-3-5-sonnet-20240620`

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

## Authentication & Security Architecture

CCProxy uses a **three-layer authentication architecture** that serves different purposes:

```
Client Applications → [ANTHROPIC_API_KEY] → CCProxy → [Claude OAuth] → Anthropic API
```

### Understanding the Two Token Types

**`ANTHROPIC_API_KEY`** (Client Configuration):
- Used by **client applications** when connecting to your CCProxy instance
- Set in client environment variables to point to your proxy
- **Purpose**: Client identification and configuration
- **Not used** by the proxy for upstream authentication

**`SECURITY__AUTH_TOKEN`** (Proxy Security - Optional):
- Used to **secure the CCProxy server itself** 
- When set: ALL client requests must include valid authentication
- When unset: Proxy runs in "open mode" (no client authentication required)
- **Purpose**: Access control for your proxy instance

### Proxy-to-Anthropic Authentication

The proxy itself uses **Claude OAuth credentials** (not environment variables) for upstream API calls:
- Credentials stored in `~/.config/claude/.credentials.json`
- In Docker: mounted via volumes from host system
- Managed by Claude CLI (`claude auth login`)

## Securing the Proxy (Optional)

You can enable token authentication for the proxy. This supports multiple header formats (`x-api-key` for Anthropic, `Authorization: Bearer` for OpenAI) for compatibility with standard client libraries.

**1. Generate a Token:**

```bash
ccproxy generate-token
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

### Configuration Scenarios

**Scenario A: Development (No Proxy Security)**
```bash
# .env file - no SECURITY__AUTH_TOKEN set
ANTHROPIC_API_KEY=dummy-key

# Client usage
export ANTHROPIC_API_KEY=dummy-key
export ANTHROPIC_BASE_URL=http://localhost:8000/api
```
*Clients can use any dummy API key. Proxy uses Claude OAuth for upstream.*

**Scenario B: Secured Proxy**
```bash
# .env file - enable proxy authentication  
ANTHROPIC_API_KEY=your-client-api-key
SECURITY__AUTH_TOKEN=your-secure-proxy-token

# Client usage
export ANTHROPIC_API_KEY=your-secure-proxy-token  # Must match SECURITY__AUTH_TOKEN
export ANTHROPIC_BASE_URL=http://localhost:8000/api
```
*Clients must use the proxy's security token. Recommended for production.*

**Scenario C: Docker Deployment**
```yaml
# docker-compose.yml
volumes:
  - ~/.config/claude:/data/home/.config/claude:ro  # Claude OAuth credentials
  - ~/.codex/auth.json:/data/home/.codex/auth.json:ro  # OpenAI credentials
```
*Proxy automatically uses mounted credentials for upstream authentication.*

## Observability

`ccproxy` includes an optional but powerful observability suite for monitoring and analytics. When enabled, it provides:

- **Prometheus Metrics:** A `/metrics` endpoint for real-time operational monitoring.
- **Access Log Storage:** Detailed request logs, including token usage and costs, are stored in a local DuckDB database.
- **Analytics API:** Endpoints to query and analyze historical usage data.
- **Real-time Dashboard:** A live web interface at `/dashboard` to visualize metrics and request streams.

These features are disabled by default and can be enabled via configuration. For a complete guide on setting up and using these features, see the [Observability Documentation](docs/observability.md).

## Troubleshooting

### Common Issues

1.  **Authentication Error:** Ensure you're using the correct mode (`/sdk` or `/api`) for your authentication method.
2.  **Claude Credentials Expired:** Run `ccproxy auth login` to refresh credentials for API mode. Run `claude /login` for SDK mode.
3.  **OpenAI/Codex Authentication Failed:**
    - Check if valid credentials exist: `ccproxy auth status`
    - Ensure you have an active ChatGPT Plus subscription
    - Try re-authenticating: `ccproxy auth login-openai` or `codex auth login`
    - Verify credentials in `$HOME/.codex/auth.json`
4.  **Codex Response API Errors:**
    - "Instruction prompt injection failed": The backend requires the Codex prompt; this is automatic
    - "Session not found": Use persistent session IDs for conversation continuity
    - "Model not available": Ensure you're using ChatGPT Plus compatible models
5.  **Missing API Auth Token:** If you've enabled security, include the token in your request headers.
6.  **Port Already in Use:** Start the server on a different port: `ccproxy --port 8001`.
7.  **Model Not Available:** Check that your subscription includes the requested model.

## Contributing

Please see [CONTRIBUTING.md](CONTRIBUTING.md) for details.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Documentation & Support

- Issues & feature requests: [GitHub Issues](https://github.com/izzoa/ccproxy-api/issues)
- Releases & discussions: [Project Home](https://github.com/izzoa/ccproxy-api)
- Upstream reference docs: [Original Project Documentation](https://caddyglow.github.io/ccproxy-api) *(feature parity notes in this fork supersede upstream limitations)*

## Acknowledgments

- [Anthropic](https://anthropic.com) for Claude and the Claude Code SDK
- The open-source community
