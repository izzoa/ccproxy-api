# Plugin API Reference

## Overview

The enhanced plugin system provides a standardized way to implement provider-specific functionality with lifecycle management, health checks, route registration, and scheduled tasks.

## Core Components

### CoreServices

Container for shared services passed to plugins during initialization.

```python
from ccproxy.core.services import CoreServices
from ccproxy.scheduler.core import Scheduler

class CoreServices:
    def __init__(
        self,
        http_client: AsyncClient,
        logger: BoundLogger,
        settings: Settings,
        scheduler: Scheduler | None = None
    )

    # Shared HTTP client for all plugins
    http_client: AsyncClient

    # Structured logger instance  
    logger: BoundLogger

    # Application settings
    settings: Settings

    # Optional scheduler for plugin tasks
    scheduler: Scheduler | None

    def get_plugin_config(self, plugin_name: str) -> dict[str, Any]
```

### ProviderPlugin Protocol

Enhanced protocol that all plugins must implement.

```python
from ccproxy.plugins.protocol import ProviderPlugin, HealthCheckResult, ScheduledTaskDefinition

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

    # Optional scheduled tasks
    def get_scheduled_tasks(self) -> list[ScheduledTaskDefinition] | None: ...
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

### ScheduledTaskDefinition

Type definition for scheduled tasks that plugins can provide.

```python
from typing import TypedDict
from ccproxy.scheduler.tasks import BaseScheduledTask

class ScheduledTaskDefinition(TypedDict, total=False):
    """Definition for a scheduled task from a plugin."""

    # Required fields
    task_name: str                          # Unique name for the task instance
    task_type: str                          # Type identifier for task registry
    task_class: type[BaseScheduledTask]     # Task class implementation
    interval_seconds: float                 # Interval between executions

    # Optional fields
    enabled: bool                           # Whether task is enabled (default: True)
    skip_initial_run: bool                  # Skip first run at startup
    # Additional kwargs can be passed for task initialization
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

    def get_scheduled_tasks(self) -> list[ScheduledTaskDefinition] | None:
        # Optional: return None if no scheduled tasks needed
        return None
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

### Scheduled Tasks Implementation

Plugins can provide scheduled tasks that run periodically:

```python
# plugins/example/tasks.py
from ccproxy.scheduler.tasks import BaseScheduledTask
import structlog

logger = structlog.get_logger(__name__)

class ExampleRefreshTask(BaseScheduledTask):
    """Example scheduled task for periodic operations."""

    def __init__(
        self,
        name: str,
        interval_seconds: float,
        detection_service: Any,  # Plugin-specific service
        enabled: bool = True,
        skip_initial_run: bool = True,
        **kwargs,
    ):
        super().__init__(
            name=name,
            interval_seconds=interval_seconds,
            enabled=enabled,
            **kwargs,
        )
        self.detection_service = detection_service
        self.skip_initial_run = skip_initial_run
        self._first_run = True

    async def run(self) -> bool:
        """Execute the task."""
        # Skip first run if configured
        if self._first_run and self.skip_initial_run:
            self._first_run = False
            logger.debug(f"Skipping initial run for {self.name}")
            return True

        self._first_run = False

        try:
            # Perform task operations
            await self.detection_service.refresh()
            logger.info(f"Task {self.name} completed successfully")
            return True
        except Exception as e:
            logger.error(f"Task {self.name} failed: {e}")
            return False

    async def setup(self) -> None:
        """Setup before task execution starts."""
        logger.info(f"Setting up task {self.name}")

    async def cleanup(self) -> None:
        """Cleanup after task execution stops."""
        logger.info(f"Cleaning up task {self.name}")
```

Then in your plugin, return task definitions:

```python
# In plugin.py
def get_scheduled_tasks(self) -> list[dict] | None:
    """Get scheduled task definitions."""
    if not self._detection_service:
        return None

    return [
        {
            "task_name": f"refresh_{self.name}",
            "task_type": "example_refresh",
            "task_class": ExampleRefreshTask,
            "interval_seconds": 3600,  # Every hour
            "enabled": True,
            "detection_service": self._detection_service,
            "skip_initial_run": True,
        }
    ]
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
from ccproxy.scheduler.core import Scheduler

# Create services with optional scheduler
scheduler = Scheduler()
services = CoreServices(
    http_client=http_client,
    logger=logger,
    settings=settings,
    scheduler=scheduler  # Optional for task support
)

registry = PluginRegistry()

# Discover and initialize all plugins
# This will also register any scheduled tasks
await registry.discover_and_initialize(services)

# Get specific plugin/adapter
plugin = registry.get_plugin("claude_sdk")
adapter = registry.get_adapter("claude_sdk")

# Health checks (runs concurrently with 10s timeout)
health_results = await registry.get_all_health_checks()

# Shutdown all plugins (also removes scheduled tasks)
await registry.shutdown_all()
```

### Task Registration Process

When a plugin provides scheduled tasks via `get_scheduled_tasks()`:

1. **Task Discovery**: Registry calls `plugin.get_scheduled_tasks()` after initialization
2. **Task Class Registration**: Task classes are registered with the task registry
3. **Scheduler Integration**: Tasks are added to the scheduler with specified intervals
4. **Lifecycle Management**: Tasks are automatically removed when plugin shuts down

```python
# Internal process in PluginRegistry
async def _register_plugin_tasks(self, plugin: ProviderPlugin) -> None:
    """Register scheduled tasks for a plugin."""
    task_definitions = plugin.get_scheduled_tasks()
    if not task_definitions:
        return

    for task_def in task_definitions:
        # Register task class
        task_registry.register(task_def["task_type"], task_def["task_class"])

        # Add to scheduler
        await scheduler.add_task(
            task_name=task_def["task_name"],
            task_type=task_def["task_type"],
            interval_seconds=task_def["interval_seconds"],
            enabled=task_def.get("enabled", True),
            **task_def  # Pass additional kwargs
        )
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
3. **Error Handling**: Always handle exceptions in plugin methods, use `raise from` for chaining
4. **Resource Cleanup**: Implement proper shutdown for connections/resources
5. **Health Checks**: Provide meaningful status and output messages
6. **Configuration**: Extend ProviderConfig for type safety
7. **Logging**: Use the provided logger with plugin context binding
8. **Scheduled Tasks**:
   - Use `skip_initial_run=True` to avoid duplicate operations at startup
   - Return `True` from `run()` for success, `False` for failure (enables backoff)
   - Keep task operations idempotent (safe to run multiple times)
   - Use reasonable intervals (avoid too frequent polling)
   - Clean up resources in task `cleanup()` method
9. **Detection Services**: Initialize during plugin startup, refresh via scheduled tasks
10. **Authentication**: Set up auth managers in `initialize()`, check status in health checks

This API provides a robust foundation for implementing provider plugins with proper lifecycle management, health monitoring, scheduled tasks, and route registration.
