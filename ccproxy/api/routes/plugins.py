"""Plugin management API endpoints."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from starlette import status

from ccproxy.auth.conditional import ConditionalAuthDep
from ccproxy.services.proxy_service import ProxyService


router = APIRouter(prefix="/plugins", tags=["plugins"])


class PluginInfo(BaseModel):
    """Plugin information model."""

    name: str
    type: str  # "builtin" or "plugin"
    status: str  # "active", "inactive", "error"
    version: str | None = None


class PluginListResponse(BaseModel):
    """Response model for plugin list."""

    plugins: list[PluginInfo]
    total: int


class PluginHealthResponse(BaseModel):
    """Response model for plugin health check."""

    plugin: str
    status: str  # "healthy", "unhealthy", "unknown"
    adapter_loaded: bool
    details: dict[str, Any] | None = None


class PluginReloadResponse(BaseModel):
    """Response model for plugin reload."""

    status: str  # "success", "error"
    message: str
    plugin: str | None = None


# Dependency to get proxy service
async def get_proxy_service(request: Request) -> ProxyService:
    """Get the proxy service instance.

    Args:
        request: FastAPI request object containing app state

    Returns:
        ProxyService instance

    Raises:
        HTTPException: If proxy service not initialized
    """
    if not hasattr(request.app.state, "proxy_service"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Proxy service not initialized",
        )

    proxy_service: ProxyService = request.app.state.proxy_service
    return proxy_service


@router.get("", response_model=PluginListResponse)
async def list_plugins(
    proxy: ProxyService = Depends(get_proxy_service),
    auth: ConditionalAuthDep = None,
) -> PluginListResponse:
    """List all loaded plugins and built-in providers.

    Returns:
        List of all available plugins and providers
    """
    plugins: list[PluginInfo] = []

    # No built-in providers - everything is a plugin now
    # All providers come from the plugin system
    if not proxy.plugin_manager:
        return PluginListResponse(plugins=plugins, total=0)

    # Plugin providers
    for name in proxy.plugin_manager.plugin_registry.list_plugins():
        plugin = proxy.plugin_manager.plugin_registry.get_plugin(name)
        plugins.append(
            PluginInfo(
                name=name,
                type="plugin",
                status="active",
                version=plugin.version if plugin else None,
            )
        )

    return PluginListResponse(plugins=plugins, total=len(plugins))


@router.get("/{plugin_name}/health", response_model=PluginHealthResponse)
async def plugin_health(
    plugin_name: str,
    proxy: ProxyService = Depends(get_proxy_service),
    auth: ConditionalAuthDep = None,
) -> PluginHealthResponse:
    """Check the health status of a specific plugin.

    Args:
        plugin_name: Name of the plugin to check

    Returns:
        Health status of the plugin

    Raises:
        HTTPException: If plugin not found
    """
    # Check plugin providers
    if not proxy.plugin_manager:
        raise HTTPException(status_code=503, detail="Plugin manager not initialized")
    adapter = proxy.plugin_manager.get_plugin_adapter(plugin_name)
    if adapter:
        # Get the plugin and run its health check if available
        plugin = proxy.plugin_manager.plugin_registry.get_plugin(plugin_name)
        if plugin and hasattr(plugin, "health_check"):
            try:
                health_result = await plugin.health_check()
                # Convert HealthCheckResult to PluginHealthResponse
                return PluginHealthResponse(
                    plugin=plugin_name,
                    status="healthy"
                    if health_result.status == "pass"
                    else "unhealthy"
                    if health_result.status == "fail"
                    else "unknown",
                    adapter_loaded=True,
                    details={
                        "type": "plugin",
                        "active": True,
                        "health_check": {
                            "status": health_result.status,
                            "output": health_result.output,
                            "version": health_result.version,
                            "details": health_result.details,
                        },
                    },
                )
            except Exception as e:
                import structlog

                logger = structlog.get_logger(__name__)
                logger.error(
                    "Plugin health check failed", plugin=plugin_name, error=str(e)
                )
                return PluginHealthResponse(
                    plugin=plugin_name,
                    status="unhealthy",
                    adapter_loaded=True,
                    details={"type": "plugin", "active": True, "error": str(e)},
                )
        else:
            # Plugin doesn't have health check, use basic status
            return PluginHealthResponse(
                plugin=plugin_name,
                status="healthy",
                adapter_loaded=True,
                details={"type": "plugin", "active": True},
            )

    # Plugin not found
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Plugin '{plugin_name}' not found",
    )


@router.post("/{plugin_name}/reload", response_model=PluginReloadResponse)
async def reload_plugin(
    plugin_name: str,
    proxy: ProxyService = Depends(get_proxy_service),
    auth: ConditionalAuthDep = None,
) -> PluginReloadResponse:
    """Reload a specific plugin.

    Args:
        plugin_name: Name of the plugin to reload

    Returns:
        Reload status

    Raises:
        HTTPException: If plugin not found or reload fails
    """

    # Check if plugin exists
    if not proxy.plugin_manager:
        raise HTTPException(status_code=503, detail="Plugin manager not initialized")
    if plugin_name not in proxy.plugin_manager.plugin_registry.list_plugins():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plugin '{plugin_name}' not found",
        )

    # Reload the plugin using the new efficient method
    success = await proxy.plugin_manager.plugin_registry.reload_plugin(plugin_name)

    if success:
        return PluginReloadResponse(
            status="success",
            message=f"Plugin '{plugin_name}' reloaded successfully",
            plugin=plugin_name,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reload plugin '{plugin_name}'",
        )


@router.post("/discover", response_model=PluginListResponse)
async def discover_plugins(
    proxy: ProxyService = Depends(get_proxy_service),
    auth: ConditionalAuthDep = None,
) -> PluginListResponse:
    """Re-discover plugins from the plugin directory.

    This will scan the plugin directory again and load any new plugins
    that have been added since the last discovery.

    Returns:
        Updated list of all plugins
    """
    # Re-initialize plugins
    if proxy.plugin_manager:
        proxy.plugin_manager.initialized = False
    await proxy.initialize_plugins()

    # Return updated list
    return await list_plugins(proxy, auth)


@router.delete("/{plugin_name}")
async def unregister_plugin(
    plugin_name: str,
    proxy: ProxyService = Depends(get_proxy_service),
    auth: ConditionalAuthDep = None,
) -> dict[str, str]:
    """Unregister a plugin.

    Args:
        plugin_name: Name of the plugin to unregister

    Returns:
        Status message

    Raises:
        HTTPException: If plugin not found or is built-in
    """

    # Unregister the plugin
    if not proxy.plugin_manager:
        raise HTTPException(status_code=503, detail="Plugin manager not initialized")
    success = await proxy.plugin_manager.plugin_registry.unregister(plugin_name)

    if success:
        # Also remove from proxy's adapter list
        if plugin_name in proxy.plugin_manager.adapters:
            del proxy.plugin_manager.adapters[plugin_name]

        return {
            "status": "success",
            "message": f"Plugin '{plugin_name}' unregistered successfully",
        }

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Plugin '{plugin_name}' not found",
    )
