# Plugin Architecture Reorganization Plan

## Executive Summary

This document outlines a comprehensive plan to reorganize the ccproxy-api project to adopt a proper plugin-based architecture. The goal is to move provider-specific code from generic service directories into dedicated plugin directories, establishing clear boundaries and responsibilities.

## Current Issues

1. **Scattered Provider Code**: Provider-specific code is distributed across multiple directories (services, core, config, adapters)
2. **Name Collisions**: Multiple classes named `OpenAIAdapter` causing confusion
3. **Mixed Responsibilities**: Generic directories contain provider-specific logic
4. **Unclear Boundaries**: No clear separation between core functionality and provider extensions
5. **Manual Route Registration**: Routes are manually registered in app.py instead of being plugin-owned
6. **Fragmented Health Checks**: Health checks are hardcoded per provider instead of being plugin-provided

## Proposed Architecture

### Core Services Container

```python
# ccproxy/core/services.py
from httpx import AsyncClient
from structlog import BoundLogger

class CoreServices:
    """Container for shared services passed to plugins."""
    
    def __init__(
        self,
        http_client: AsyncClient,
        logger: BoundLogger,
        settings: Settings
    ):
        self.http_client = http_client
        self.logger = logger
        self.settings = settings
```

### Enhanced Plugin Protocol

```python
# ccproxy/plugins/protocol.py
from typing import Protocol, runtime_checkable, Any
from fastapi import APIRouter, FastAPI
from pydantic import BaseModel
from ccproxy.core.services import CoreServices

class ProviderConfig(BaseModel):
    """Base configuration for all provider plugins."""
    enabled: bool = True
    priority: int = 0

class HealthCheckResult(BaseModel):
    """Standardized health check result following IETF format."""
    status: Literal["pass", "warn", "fail"]
    componentId: str
    componentType: str = "provider_plugin"
    output: str | None = None
    version: str | None = None
    details: dict[str, Any] | None = None

@runtime_checkable
class ProviderPlugin(Protocol):
    """Enhanced protocol for provider plugins."""
    
    @property
    def name(self) -> str:
        """Plugin name."""
        ...
    
    @property
    def version(self) -> str:
        """Plugin version."""
        ...
    
    @property
    def router_prefix(self) -> str:
        """Unique route prefix for this plugin (e.g., '/claude', '/codex')."""
        ...
    
    async def initialize(self, services: CoreServices) -> None:
        """Initialize plugin with shared services. Called once on startup."""
        ...
    
    async def shutdown(self) -> None:
        """Perform graceful shutdown. Called once on app shutdown."""
        ...
    
    def create_adapter(self) -> BaseAdapter:
        """Create adapter instance."""
        ...
    
    def create_config(self) -> ProviderConfig:
        """Create provider configuration from settings."""
        ...
    
    async def validate(self) -> bool:
        """Validate plugin is ready."""
        ...
    
    def get_routes(self) -> APIRouter | None:
        """Get plugin-specific routes (optional)."""
        ...
    
    async def health_check(self) -> HealthCheckResult:
        """Perform health check following IETF format."""
        ...
```

### Configuration Management

The configuration system works hierarchically:

1. **Main configuration** (`ccproxy/config/settings.py`) defines plugin sections:
```python
class Settings(BaseSettings):
    # Core settings
    server: ServerSettings
    
    # Plugin configurations (loaded from env/files)
    plugins: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Plugin-specific configurations"
    )
    
    # Example structure in config file:
    # [plugins.claude_sdk]
    # cli_path = "/usr/local/bin/claude"
    # builtin_permissions = true
    #
    # [plugins.codex]
    # api_key = "..."
```

2. **Plugin configuration classes** extend `ProviderConfig`:
```python
# plugins/claude_sdk/config.py
from ccproxy.plugins.protocol import ProviderConfig

class ClaudeSettings(ProviderConfig):
    """Claude SDK specific configuration."""
    cli_path: str | None = None
    builtin_permissions: bool = True
    # ... other Claude-specific settings
```

3. **Plugins receive their config section** during initialization:
```python
# In plugin initialization
async def initialize(self, services: CoreServices) -> None:
    # Get plugin-specific config from main settings
    plugin_config = services.settings.plugins.get(self.name, {})
    self._config = ClaudeSettings(**plugin_config)
```

## Plugin Directory Structure

### Claude SDK Plugin
```
plugins/
├── claude_sdk/
│   ├── __init__.py                    # Plugin entry point with __all__ exports
│   ├── plugin.py                      # ClaudeSDKPlugin class
│   ├── adapter.py                     # ClaudeSDKAdapter (from BaseAdapter)
│   ├── config.py                      # ClaudeSettings (extends ProviderConfig)
│   ├── detection_service.py           # Claude CLI detection service
│   ├── routes.py                      # Claude-specific routes
│   ├── health.py                      # Claude health check implementation
│   ├── transformers/
│   │   ├── __init__.py
│   │   ├── request.py                 # Claude request transformers
│   │   └── response.py                # Claude response transformers
│   └── utils/
│       ├── __init__.py
│       └── cli_detector.py            # CLI detection utilities
```

### Codex Plugin
```
plugins/
├── codex/
│   ├── __init__.py                    # Plugin entry point with __all__ exports
│   ├── plugin.py                      # CodexPlugin class
│   ├── adapter.py                     # CodexAdapter (from BaseAdapter)
│   ├── config.py                      # CodexSettings (extends ProviderConfig)
│   ├── detection_service.py           # Codex CLI detection service
│   ├── routes.py                      # Codex-specific routes (/codex/*)
│   ├── health.py                      # Codex health check implementation
│   ├── transformers/
│   │   ├── __init__.py
│   │   ├── request.py                 # Codex request transformers
│   │   └── response.py                # Codex response transformers
│   └── utils/
│       ├── __init__.py
│       └── cli_detector.py            # CLI detection utilities
```

### OpenAI Plugin (Existing)
```
plugins/
├── openai/
│   ├── __init__.py                    # Plugin entry point with __all__ exports
│   ├── plugin.py                      # OpenAIPluginAdapter class
│   ├── format_adapter.py              # OpenAIFormatAdapter (moved from ccproxy/adapters)
│   ├── routes.py                      # OpenAI-specific routes
│   ├── health.py                      # OpenAI health check implementation
│   └── config.py                      # OpenAISettings (extends ProviderConfig)
```

## Plugin Discovery and Loading

### Discovery Mechanism

```python
# ccproxy/plugins/loader.py
import importlib.metadata
import importlib.util
from pathlib import Path
from typing import list[ProviderPlugin]

class PluginLoader:
    """Handles plugin discovery and loading."""
    
    async def discover_plugins(self) -> list[ProviderPlugin]:
        """Discover plugins from multiple sources."""
        plugins = []
        
        # 1. Load from entry points (for installed packages)
        plugins.extend(self._load_from_entry_points())
        
        # 2. Load from plugins directory (for development)
        plugins.extend(self._load_from_directory())
        
        return plugins
    
    def _load_from_entry_points(self) -> list[ProviderPlugin]:
        """Load plugins registered via setuptools entry points."""
        plugins = []
        for entry_point in importlib.metadata.entry_points(group='ccproxy.plugins'):
            try:
                plugin_class = entry_point.load()
                plugins.append(plugin_class())
            except Exception as e:
                logger.error(f"Failed to load plugin {entry_point.name}: {e}")
        return plugins
    
    def _load_from_directory(self) -> list[ProviderPlugin]:
        """Load plugins from the plugins/ directory."""
        plugins = []
        plugin_dir = Path(__file__).parent.parent / 'plugins'
        
        for subdir in plugin_dir.iterdir():
            if not subdir.is_dir() or subdir.name.startswith('_'):
                continue
                
            try:
                # Import the plugin module
                spec = importlib.util.spec_from_file_location(
                    f"plugins.{subdir.name}.plugin",
                    subdir / "plugin.py"
                )
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    
                    # Look for Plugin class
                    if hasattr(module, 'Plugin'):
                        plugins.append(module.Plugin())
            except Exception as e:
                logger.error(f"Failed to load plugin from {subdir}: {e}")
                
        return plugins
```

## Implementation Examples

### Claude SDK Plugin Implementation

```python
# plugins/claude_sdk/plugin.py
from fastapi import APIRouter
from ccproxy.plugins.protocol import ProviderPlugin, HealthCheckResult
from ccproxy.services.adapters.base import BaseAdapter
from ccproxy.core.services import CoreServices
from .adapter import ClaudeSDKAdapter
from .config import ClaudeSettings
from .health import claude_health_check
from .routes import router as claude_router

class Plugin(ProviderPlugin):  # Standardized class name for discovery
    """Claude SDK provider plugin."""
    
    def __init__(self):
        self._name = "claude_sdk"
        self._version = "1.0.0"
        self._router_prefix = "/claude"
        self._adapter = None
        self._config = None
        self._services = None
        
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
        """Initialize plugin with shared services."""
        self._services = services
        
        # Load plugin-specific configuration
        plugin_config = services.settings.plugins.get(self.name, {})
        self._config = ClaudeSettings(**plugin_config)
        
        # Initialize adapter with shared HTTP client
        self._adapter = ClaudeSDKAdapter(
            http_client=services.http_client,
            logger=services.logger.bind(plugin=self.name)
        )
        
        # Perform any startup tasks (e.g., CLI detection)
        if self._config.auto_detect_cli:
            await self._detect_cli_headers()
    
    async def shutdown(self) -> None:
        """Cleanup on shutdown."""
        if self._adapter:
            await self._adapter.cleanup()
    
    def create_adapter(self) -> BaseAdapter:
        return self._adapter
    
    def create_config(self) -> ClaudeSettings:
        return self._config
    
    async def validate(self) -> bool:
        """Check if Claude CLI is available."""
        if not self._config:
            return False
        cli_path, _ = self._config.find_claude_cli()
        return cli_path is not None
    
    def get_routes(self) -> APIRouter | None:
        """Return Claude-specific routes."""
        return claude_router
    
    async def health_check(self) -> HealthCheckResult:
        """Perform health check for Claude SDK."""
        return await claude_health_check(self._config)
```

### Concurrent Health Check Aggregation

```python
# ccproxy/api/routes/health.py (modified)
import asyncio
from typing import Any

@router.get("/health")
async def detailed_health_check(response: Response, request: Request) -> dict[str, Any]:
    """Comprehensive health check including plugin health."""
    
    # ... existing health checks ...
    
    # Collect plugin health checks concurrently
    plugin_health = {}
    if hasattr(request.app.state, "proxy_service"):
        proxy_service = request.app.state.proxy_service
        plugins = proxy_service.plugin_registry.get_all_plugins()
        
        # Create health check tasks
        health_tasks = []
        plugin_names = []
        for plugin in plugins:
            if hasattr(plugin, 'health_check'):
                health_tasks.append(
                    asyncio.create_task(plugin.health_check())
                )
                plugin_names.append(plugin.name)
        
        # Execute concurrently with timeout
        if health_tasks:
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*health_tasks, return_exceptions=True),
                    timeout=10.0  # 10 second total timeout
                )
                
                for name, result in zip(plugin_names, results):
                    if isinstance(result, Exception):
                        plugin_health[f"plugin_{name}"] = [{
                            "status": "fail",
                            "componentId": f"plugin-{name}",
                            "componentType": "provider_plugin",
                            "output": f"Health check failed: {str(result)}"
                        }]
                    else:
                        # Result is a HealthCheckResult model
                        plugin_health[f"plugin_{name}"] = [result.model_dump()]
            except asyncio.TimeoutError:
                plugin_health["plugin_timeout"] = [{
                    "status": "fail",
                    "componentId": "plugin-health",
                    "componentType": "system",
                    "output": "Plugin health checks timed out after 10 seconds"
                }]
    
    # Merge plugin health with existing checks
    all_checks = {
        **existing_checks,  # oauth2, etc.
        **plugin_health
    }
    
    # Determine overall status
    overall_status = "pass"
    for check_list in all_checks.values():
        for check in check_list:
            if check.get("status") == "fail":
                overall_status = "fail"
                break
            elif check.get("status") == "warn" and overall_status == "pass":
                overall_status = "warn"
    
    return {
        "status": overall_status,
        "version": __version__,
        "serviceId": "claude-code-proxy",
        "checks": all_checks
    }
```

### Plugin Registration in App

```python
# ccproxy/api/app.py (modified lifespan)
from ccproxy.plugins.loader import PluginLoader
from ccproxy.core.services import CoreServices

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager."""
    settings = get_settings()
    
    # Create shared services
    async with AsyncClient() as http_client:
        services = CoreServices(
            http_client=http_client,
            logger=logger,
            settings=settings
        )
        
        # Discover and initialize plugins
        loader = PluginLoader()
        plugins = await loader.discover_plugins()
        
        # Initialize plugins with shared services
        for plugin in plugins:
            try:
                await plugin.initialize(services)
                
                # Register plugin routes
                if plugin.get_routes():
                    app.include_router(
                        plugin.get_routes(),
                        prefix=plugin.router_prefix,
                        tags=[f"plugin-{plugin.name}"]
                    )
                    
                logger.info(f"Initialized plugin: {plugin.name} v{plugin.version}")
            except Exception as e:
                logger.error(f"Failed to initialize plugin {plugin.name}: {e}")
        
        # Store plugins in app state
        app.state.plugins = plugins
        app.state.services = services
        
        yield
        
        # Shutdown plugins
        for plugin in plugins:
            try:
                await plugin.shutdown()
            except Exception as e:
                logger.error(f"Error shutting down plugin {plugin.name}: {e}")
```

## File Movement Plan

### Phase 1: Claude SDK Plugin

**Move FROM:**
- `ccproxy/services/claude_detection_service.py` → `plugins/claude_sdk/detection_service.py`
- `ccproxy/config/claude.py` → `plugins/claude_sdk/config.py` (extend ProviderConfig)
- `ccproxy/core/http_transformers.py` (Claude parts) → `plugins/claude_sdk/transformers/`
- `ccproxy/services/claude_service.py` (if exists) → `plugins/claude_sdk/adapter.py`

**Create NEW:**
- `plugins/claude_sdk/__init__.py` with `__all__ = ['Plugin']`
- `plugins/claude_sdk/plugin.py` - Main plugin class
- `plugins/claude_sdk/health.py` - Health check implementation
- `plugins/claude_sdk/routes.py` - Claude-specific routes

### Phase 2: Codex Plugin

**Move FROM:**
- `ccproxy/services/codex_detection_service.py` → `plugins/codex/detection_service.py`
- `ccproxy/config/codex.py` → `plugins/codex/config.py` (extend ProviderConfig)
- `ccproxy/core/codex_transformers.py` → `plugins/codex/transformers/`
- `ccproxy/api/routes/codex.py` (if exists) → `plugins/codex/routes.py`

**Create NEW:**
- `plugins/codex/__init__.py` with `__all__ = ['Plugin']`
- `plugins/codex/plugin.py` - Main plugin class
- `plugins/codex/health.py` - Health check implementation
- `plugins/codex/adapter.py` - Codex adapter implementation

### Phase 3: OpenAI Plugin Clarification

**Rename for Clarity:**
- `plugins/openai_plugin.py::OpenAIAdapter` → `OpenAIPluginAdapter`
- `ccproxy/adapters/openai/adapter.py::OpenAIAdapter` → Move to `plugins/openai/format_adapter.py::OpenAIFormatAdapter`

**Consolidate:**
- Move all OpenAI-related code to `plugins/openai/` directory
- Create `plugins/openai/__init__.py` with `__all__ = ['Plugin']`
- Create `plugins/openai/health.py` for health checks

## Import Updates Required

### Files Requiring Import Updates

1. **ccproxy/api/app.py**
   - Remove direct imports of detection services
   - Add plugin loader and registration logic

2. **ccproxy/services/proxy_service.py**
   - Update to use plugin-provided configurations
   - Remove hardcoded provider logic

3. **ccproxy/utils/startup_helpers.py**
   - Move provider-specific startup to plugin initialization
   - Keep only core startup logic

4. **Tests**
   - Update all test imports to use new plugin paths
   - Create plugin-specific test directories

## Testing Strategy

### Plugin Contract Test Harness

```python
# tests/test_plugin_contract.py
import pytest
from ccproxy.plugins.protocol import ProviderPlugin
from ccproxy.plugins.loader import PluginLoader

@pytest.mark.parametrize("plugin", PluginLoader().discover_plugins())
def test_plugin_contract(plugin):
    """Verify all plugins conform to the protocol."""
    assert isinstance(plugin, ProviderPlugin)
    assert plugin.name
    assert plugin.version
    assert plugin.router_prefix
    assert plugin.router_prefix.startswith('/')
    
    # Verify no route prefix collisions
    all_plugins = PluginLoader().discover_plugins()
    prefixes = [p.router_prefix for p in all_plugins]
    assert len(prefixes) == len(set(prefixes)), "Route prefix collision detected"

@pytest.mark.asyncio
@pytest.mark.parametrize("plugin", PluginLoader().discover_plugins())
async def test_plugin_lifecycle(plugin, mock_services):
    """Test plugin initialization and shutdown."""
    await plugin.initialize(mock_services)
    assert await plugin.validate()
    
    health = await plugin.health_check()
    assert health.status in ["pass", "warn", "fail"]
    
    await plugin.shutdown()
```

## Benefits of This Architecture

1. **Clear Separation**: Each provider is self-contained in its plugin directory
2. **No Name Collisions**: Clear naming conventions prevent confusion
3. **Plugin-Owned Routes**: Each plugin manages its own routes with unique prefixes
4. **Unified Health Checks**: Plugins provide typed health checks in standard format
5. **Easy Extension**: New providers can be added as plugins without modifying core
6. **Better Testing**: Plugin-specific tests are isolated
7. **Configuration Management**: Centralized config with plugin-specific sections
8. **Resource Efficiency**: Shared HTTP client and logging across plugins
9. **Resilient Loading**: Failed plugins don't crash the application
10. **Concurrent Operations**: Health checks run in parallel with timeouts

## Migration Strategy

### Phase 1: Infrastructure (Week 1)
- Create enhanced plugin protocol with lifecycle hooks
- Implement plugin loader with entry point support
- Create CoreServices container
- Update app.py for dynamic plugin registration
- Create plugin contract test harness

### Phase 2: Claude SDK Plugin (Week 2)
- Create plugin directory structure
- Move files and update imports
- Extend ClaudeSettings from ProviderConfig
- Implement Plugin class with lifecycle methods
- Add typed health checks
- Test thoroughly

### Phase 3: Codex Plugin (Week 3)
- Create plugin directory structure
- Move files and update imports
- Extend CodexSettings from ProviderConfig
- Implement Plugin class with lifecycle methods
- Add typed health checks
- Test thoroughly

### Phase 4: OpenAI Clarification (Week 4)
- Rename conflicting classes
- Consolidate OpenAI code into plugin directory
- Implement lifecycle methods
- Add health checks
- Test thoroughly

### Phase 5: Cleanup and Testing (Week 5)
- Remove old files and imports
- Update all remaining imports
- Run comprehensive test suite
- Update documentation
- Performance benchmarking

## Quality Assurance

### Pre-Migration Checklist
- [ ] Create comprehensive test suite with >80% coverage
- [ ] Document all existing API endpoints
- [ ] Create git tag for rollback point
- [ ] Measure current performance benchmarks

### During Migration
- [ ] Test each phase independently
- [ ] Run plugin contract tests after each phase
- [ ] Verify no route conflicts
- [ ] Check resource cleanup in shutdown

### Post-Migration Validation
- [ ] All tests passing
- [ ] Health checks working concurrently
- [ ] Routes properly registered with prefixes
- [ ] No import errors
- [ ] Performance benchmarks maintained or improved
- [ ] Plugin discovery working for both directory and entry points

## Risk Mitigation

1. **Plugin Isolation**: Exception handling prevents one plugin from crashing others
2. **Gradual Migration**: Move one provider at a time with testing between phases
3. **Rollback Plan**: Git tags at each phase for easy rollback
4. **Timeout Protection**: Bounded timeouts for health checks and initialization
5. **Explicit API Surface**: `__all__` exports in `__init__.py` files

## Success Criteria

1. All provider-specific code moved to plugin directories
2. No provider-specific imports in core modules
3. Dynamic route registration with unique prefixes working
4. Concurrent plugin health checks integrated into root health endpoint
5. All existing tests passing with >80% coverage
6. New plugin can be added without modifying core code
7. Plugin discovery works for both development and installed packages
8. Shared resources (HTTP client, logger) properly managed
9. Clean shutdown with resource cleanup

## Documentation Requirements

### Plugin Development Guide
Create `docs/plugin-development.md` with:
- Plugin protocol specification
- Directory structure requirements
- Configuration integration guide
- Route prefix conventions
- Health check format
- Testing requirements
- Entry point registration for packages

### Migration Guide
Update `docs/migration.md` with:
- Import path changes
- Configuration changes
- API endpoint changes (if any)
- Testing updates

## Next Steps

1. Review and approve this updated plan
2. Create feature branch: `refactor/plugin-architecture`
3. Set up plugin contract tests in CI
4. Begin Phase 1 implementation
5. Schedule code reviews at each phase completion

---

*Document Created: 2024-12-14*
*Status: READY FOR IMPLEMENTATION*
*Incorporates feedback from: Gemini 2.5 Pro, O3*