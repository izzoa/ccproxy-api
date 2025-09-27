# CCProxy API Server

CCProxy is a local, plugin-based reverse proxy that unifies access to
multiple AI providers (e.g., Claude SDK/API and OpenAI Codex) behind a
consistent API. It ships with bundled plugins for providers, logging,
tracing, metrics, analytics, and more.

## Supported Providers

- Anthropic Claude API/SDK (OAuth2 flow or Claude CLI/SDK token files)
- OpenAI Codex (ChatGPT backend Responses API using OAuth for paid/pro accounts)
- GitHub Copilot (chat and completions for free, paid, or business accounts)

Each provider adapter exposes the same surface area: OpenAI Chat
Completions, OpenAI Responses, and Anthropic Messages. The proxy maintains a
shared model-mapping layer so you can reuse the same `model` identifier
across providers without rewriting client code.

Authentication can reuse existing provider files (e.g., Claude CLI SDK
tokens and the Codex CLI credential store), or you can run
`ccproxy auth login <provider>` to complete the OAuth flow from the CLI;
stored secrets are picked up automatically by the proxy.

## Extensibility

CCProxy's plugin system lets you add instrumentation and storage layers
without patching the core server. Bundled plugins currently include:

- `access_log`: structured request logging with optional client-facing output
- `analytics`: DuckDB-backed request analytics API surface
- `metrics`: Prometheus-compatible metrics with optional Pushgateway support

## Quick Links

- Docs site entry: `docs/index.md`
- Getting started: `docs/getting-started/quickstart.md`
- Configuration reference: `docs/getting-started/configuration.md`
- Examples: `docs/examples.md`
- Migration (0.2): `docs/migration/0.2-plugin-first.md`

## Plugin Config Quickstart

The plugin system is enabled by default (`enable_plugins = true`), and all
discovered plugins load automatically when no additional filters are set. Use
these knobs to adjust what runs:

- `enabled_plugins`: optional allow list; when set, only the listed plugins run.
- `disabled_plugins`: optional block list applied when `enabled_plugins` is not
  set.
- `plugins.<name>.enabled`: per-plugin flag (defaults to `true`) that you can
  override in TOML or environment variables. Any plugin set to `false` is added
  to the deny list alongside `disabled_plugins` during startup.

During startup we merge `disabled_plugins` and any `plugins.<name>.enabled = false`
entries into a single deny list. At runtime the loader checks the allow list
first and then confirms the plugin is not deny listed. Configure plugins under
`plugins.<name>` in TOML or via nested environment variables.

Use `ccproxy plugins list` to inspect discovered plugins and
`ccproxy plugins settings <name>` to review configuration fields.

### TOML example (`.ccproxy.toml`)

```toml
enable_plugins = true
# enabled_plugins = ["metrics", "analytics"]  # Optional allow list
disabled_plugins = ["duckdb_storage"]          # Optional block list

[plugins.access_log]
client_enabled = true
client_format = "structured"
client_log_file = "/tmp/ccproxy/access.log"

[plugins.request_tracer]
json_logs_enabled = true
raw_http_enabled = true
log_dir = "/tmp/ccproxy/traces"

[plugins.duckdb_storage]
enabled = false

[plugins.analytics]
enabled = true

# Metrics plugin
[plugins.metrics]
enabled = true
# pushgateway_enabled = true
# pushgateway_url = "http://localhost:9091"
# pushgateway_job = "ccproxy"
# pushgateway_push_interval = 60
```

### Environment variables (nested with `__`)

```bash
export ENABLE_PLUGINS=true
# export ENABLED_PLUGINS="metrics,analytics"  # Optional allow list
export DISABLED_PLUGINS="duckdb_storage"      # Optional block list
export PLUGINS__ACCESS_LOG__ENABLED=true
export PLUGINS__ACCESS_LOG__CLIENT_ENABLED=true
export PLUGINS__ACCESS_LOG__CLIENT_FORMAT=structured
export PLUGINS__ACCESS_LOG__CLIENT_LOG_FILE=/tmp/ccproxy/access.log

export PLUGINS__REQUEST_TRACER__ENABLED=true
export PLUGINS__REQUEST_TRACER__JSON_LOGS_ENABLED=true
export PLUGINS__REQUEST_TRACER__RAW_HTTP_ENABLED=true
export PLUGINS__REQUEST_TRACER__LOG_DIR=/tmp/ccproxy/traces

export PLUGINS__DUCKDB_STORAGE__ENABLED=true
export PLUGINS__ANALYTICS__ENABLED=true
export PLUGINS__METRICS__ENABLED=true
# export PLUGINS__METRICS__PUSHGATEWAY_ENABLED=true
# export PLUGINS__METRICS__PUSHGATEWAY_URL=http://localhost:9091
```

## Running

```bash
ccproxy serve  # default on localhost:8000
```

## License

See `LICENSE`.
