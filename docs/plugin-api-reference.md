# Plugin API Reference

## Overview

The enhanced plugin system provides a standardized way to implement provider-specific functionality with lifecycle management, health checks, and route registration.

## Core Components

### CoreServices

Container for shared services passed to plugins during initialization.

```python
from ccproxy.core.services import CoreServices

class CoreServices:
    def __init__(self, http_client: AsyncClient, logger: BoundLogger, settings: Settings)
    
    # Shared HTTP client for all plugins
    http_client: AsyncClient
    
    # Structured logger instance  
    logger: BoundLogger
    
    # Application settings
    settings: Settings
    
    def get_plugin_config(self, plugin_name: str) -> dict[str, Any]
```

### ProviderPlugin Protocol

Enhanced protocol that all plugins must implement.

```python
from ccproxy.plugins.protocol import ProviderPlugin, HealthCheckResult

class ProviderPlugin(Protocol):
    # Required properties
    @property
    def name(self) -> str: ...
    
    @property  
    def version(self) -> str: ...
    
    @property
    def router_prefix(self) -> str: ...  # e.g., "/claude", "/codex"
    
    # Lifecycle methods
    async def initialize(self, services: CoreServices) -> None: ...
    async def shutdown(self) -> None: ...
    
    # Plugin functionality
    def create_adapter(self) -> BaseAdapter: ...
    def create_config(self) -> ProviderConfig: ...
    async def validate(self) -> bool: ...
    
    # Optional route registration
    def get_routes(self) -> APIRouter | None: ...
    
    # Health monitoring
    async def health_check(self) -> HealthCheckResult: ...
```

### HealthCheckResult

Standardized health check format following IETF RFC specification.

```python
from ccproxy.plugins.protocol import HealthCheckResult

class HealthCheckResult(BaseModel):
    status: Literal["pass", "warn", "fail"]
    componentId: str                    # e.g., "plugin-claude_sdk"
    componentType: str = "provider_plugin"
    output: str | None = None          # Human-readable status message
    version: str | None = None         # Plugin version
    details: dict[str, Any] | None = None  # Additional diagnostic info
```

## Plugin Implementation Guide

### Directory Structure

```
plugins/
├── {plugin_name}/
│   ├── __init__.py          # Export Plugin class
│   ├── plugin.py           # Main Plugin implementation
│   ├── adapter.py          # BaseAdapter implementation  
│   ├── config.py           # ProviderConfig extension
│   ├── routes.py           # FastAPI routes (optional)
│   ├── health.py           # Health check implementation
│   ├── transformers/       # Request/response transformers
│   └── utils/              # Plugin-specific utilities
```

### Minimal Plugin Implementation

```python
# plugins/example/plugin.py
from fastapi import APIRouter
from ccproxy.plugins.protocol import ProviderPlugin, HealthCheckResult
from ccproxy.core.services import CoreServices
from ccproxy.services.adapters.base import BaseAdapter
from .adapter import ExampleAdapter
from .config import ExampleConfig

class Plugin(ProviderPlugin):  # Must be named "Plugin"
    def __init__(self):
        self._name = "example"
        self._version = "1.0.0" 
        self._router_prefix = "/example"
        self._services = None
        self._config = None
        self._adapter = None
    
    @property
    def name(self) -> str:
        return self._name
    
    @property
    def version(self) -> str:
        return self._version
        
    @property
    def router_prefix(self) -> str:
        return self._router_prefix
    
    async def initialize(self, services: CoreServices) -> None:
        self._services = services
        plugin_config = services.get_plugin_config(self.name)
        self._config = ExampleConfig(**plugin_config)
        
        self._adapter = ExampleAdapter(
            http_client=services.http_client,
            logger=services.logger.bind(plugin=self.name)
        )
    
    async def shutdown(self) -> None:
        if self._adapter:
            await self._adapter.cleanup()
    
    def create_adapter(self) -> BaseAdapter:
        return self._adapter
    
    def create_config(self) -> ExampleConfig:
        return self._config
    
    async def validate(self) -> bool:
        return self._config is not None
    
    def get_routes(self) -> APIRouter | None:
        # Optional: return FastAPI router
        return None
    
    async def health_check(self) -> HealthCheckResult:
        return HealthCheckResult(
            status="pass",
            componentId=f"plugin-{self.name}",
            output=f"{self.name} plugin is healthy",
            version=self.version
        )
```

### Plugin Configuration

```python
# plugins/example/config.py
from ccproxy.models.provider import ProviderConfig

class ExampleConfig(ProviderConfig):
    """Example plugin configuration extending base ProviderConfig."""
    
    api_key: str | None = None
    endpoint: str = "https://api.example.com"
    timeout: int = 30
    enabled: bool = True
    priority: int = 0
```

### Plugin Discovery

Plugins are discovered through two mechanisms:

1. **Directory Discovery** (Development): Scans `plugins/` directory for subdirectories containing `plugin.py` with a `Plugin` class.

2. **Entry Points** (Production): Uses setuptools entry points in `pyproject.toml`:
   ```toml
   [project.entry-points."ccproxy.plugins"]
   example = "plugins.example.plugin:Plugin"
   ```

## Plugin Registry API

### Registration and Lifecycle

```python
from ccproxy.plugins import PluginRegistry
from ccproxy.core.services import CoreServices

registry = PluginRegistry()

# Discover and initialize all plugins
await registry.discover_and_initialize(services)

# Get specific plugin/adapter
plugin = registry.get_plugin("claude_sdk")
adapter = registry.get_adapter("claude_sdk") 

# Health checks (runs concurrently with 10s timeout)
health_results = await registry.get_all_health_checks()

# Shutdown all plugins
await registry.shutdown_all()
```

## Route Registration

### Automatic Route Registration

When a plugin implements `get_routes()`, routes are automatically registered with the plugin's prefix:

```python
# In app.py lifespan
for plugin in plugins:
    if plugin.get_routes():
        app.include_router(
            plugin.get_routes(),
            prefix=plugin.router_prefix,  # e.g., "/claude"
            tags=[f"plugin-{plugin.name}"]
        )
```

### Route Prefix Conventions

- `/claude` - Claude SDK plugin routes
- `/codex` - Codex plugin routes  
- `/openai` - OpenAI format adapter routes
- No conflicts: Registry validates unique prefixes

## Health Check Integration

### Concurrent Health Monitoring

```python
# Health checks run concurrently across all plugins
health_results = await registry.get_all_health_checks()

# Results format:
{
    "claude_sdk": HealthCheckResult(status="pass", ...),
    "codex": HealthCheckResult(status="warn", ...),
    "timeout": HealthCheckResult(status="fail", ...)  # If timeout occurs
}
```

### Integration with /health Endpoint

Plugin health checks are automatically included in the main `/health` endpoint response under plugin-specific keys.

## Error Handling

### Plugin Isolation

- Failed plugin initialization doesn't crash the application
- Plugin exceptions are logged and isolated
- Registry removes failed plugins automatically
- Timeout protection for health checks and initialization

### Graceful Degradation

- Application continues running even if plugins fail
- Health checks report plugin-specific failures
- Route registration is optional and failure-tolerant

## Migration Notes

### Legacy Plugin Support

The loader supports both legacy (`*_plugin.py` files) and enhanced (directory-based) plugins:

- Legacy plugins are loaded as fallback
- Enhanced plugins take precedence
- Duplicate names are deduplicated (enhanced wins)

### Configuration Integration

Plugins receive configuration through `CoreServices.get_plugin_config()`, which maps to existing provider configs:

- `claude_sdk` → `settings.claude`
- `codex` → `settings.codex`  
- `openai` → empty dict (for now)

This ensures backward compatibility during the migration process.

## Best Practices

1. **Plugin Naming**: Use snake_case for plugin names matching directory names
2. **Route Prefixes**: Start with `/` and use plugin name (e.g., `/claude_sdk`)
3. **Error Handling**: Always handle exceptions in plugin methods
4. **Resource Cleanup**: Implement proper shutdown for connections/resources
5. **Health Checks**: Provide meaningful status and output messages
6. **Configuration**: Extend ProviderConfig for type safety
7. **Logging**: Use the provided logger with plugin context binding

This API provides a robust foundation for implementing provider plugins with proper lifecycle management, health monitoring, and route registration.
