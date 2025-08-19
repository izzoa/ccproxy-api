"""Shared dependencies for CCProxy API Server."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, cast

import httpx
from fastapi import Depends, Request
from structlog import get_logger

from ccproxy.config.settings import Settings, get_settings
from ccproxy.core.http import BaseProxyClient
from ccproxy.core.http_client import get_shared_http_client
from ccproxy.observability import PrometheusMetrics, get_metrics
from ccproxy.observability.storage.duckdb_simple import SimpleDuckDBStorage
from ccproxy.services.credentials.manager import CredentialsManager
from ccproxy.services.proxy_service import ProxyService


if TYPE_CHECKING:
    from plugins.claude_api.adapter import ClaudeAPIAdapter
    from plugins.codex.adapter import CodexAdapter


logger = get_logger(__name__)


def get_cached_settings(request: Request) -> Settings:
    """Get cached settings from app state.

    This avoids recomputing settings on every request by using the
    settings instance computed during application startup.

    Args:
        request: FastAPI request object

    Returns:
        Settings instance from app state

    Raises:
        RuntimeError: If settings are not available in app state
    """
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        # Fallback to get_settings() for safety, but this should not happen
        # in normal operation after lifespan startup
        logger.warning(
            "Settings not found in app state, falling back to get_settings()"
        )
        settings = get_settings()
    return settings


async def get_http_client(
    settings: Annotated[Settings, Depends(get_cached_settings)],
) -> httpx.AsyncClient:
    """Get shared HTTP client instance.

    Args:
        settings: Application settings dependency

    Returns:
        Shared HTTP client instance
    """
    logger.debug("Getting shared HTTP client instance")
    return await get_shared_http_client(settings)


def get_credentials_manager(
    settings: Annotated[Settings, Depends(get_cached_settings)],
) -> CredentialsManager:
    """Get credentials manager instance.

    Args:
        settings: Application settings dependency

    Returns:
        Credentials manager instance
    """
    logger.debug("Creating credentials manager instance")
    return CredentialsManager(config=settings.auth)


def get_proxy_service(
    request: Request,
    settings: Annotated[Settings, Depends(get_cached_settings)],
    credentials_manager: Annotated[
        CredentialsManager, Depends(get_credentials_manager)
    ],
) -> ProxyService:
    """Get proxy service instance.

    Args:
        request: FastAPI request object (for app state access)
        settings: Application settings dependency
        credentials_manager: Credentials manager dependency

    Returns:
        Proxy service instance
    """
    logger.debug("get_proxy_service")

    # Check if proxy service is already initialized in app state
    proxy_service = getattr(request.app.state, "proxy_service", None)
    if proxy_service:
        typed_proxy_service: ProxyService = proxy_service
        return typed_proxy_service

    # Fallback to creating a new instance (for backward compatibility)
    logger.warning("Proxy service not found in app state, creating new instance")

    # Create HTTP client for proxy
    from ccproxy.core.http import HTTPXClient
    from ccproxy.services.container import ServiceContainer

    http_client = HTTPXClient()
    proxy_client = BaseProxyClient(http_client)

    # Get global metrics instance
    metrics = get_metrics()

    # Use ServiceContainer to create ProxyService
    container = ServiceContainer(settings)
    return container.create_proxy_service(
        proxy_client=proxy_client,
        credentials_manager=credentials_manager,
        metrics=metrics,
    )


def get_observability_metrics() -> PrometheusMetrics:
    """Get observability metrics instance.

    Returns:
        PrometheusMetrics instance
    """
    logger.debug("get_observability_metrics")
    return get_metrics()


async def get_log_storage(request: Request) -> SimpleDuckDBStorage | None:
    """Get log storage from app state.

    Args:
        request: FastAPI request object

    Returns:
        SimpleDuckDBStorage instance if available, None otherwise
    """
    return getattr(request.app.state, "log_storage", None)


async def get_duckdb_storage(request: Request) -> SimpleDuckDBStorage | None:
    """Get DuckDB storage from app state (backward compatibility).

    Args:
        request: FastAPI request object

    Returns:
        SimpleDuckDBStorage instance if available, None otherwise
    """
    # Try new name first, then fall back to old name for backward compatibility
    storage = getattr(request.app.state, "log_storage", None)
    if storage is None:
        storage = getattr(request.app.state, "duckdb_storage", None)
    return storage


# Plugin adapter dependencies
def get_claude_api_adapter(proxy_service: ProxyService) -> ClaudeAPIAdapter:
    """Get Claude API adapter instance.

    Args:
        proxy_service: Proxy service dependency

    Returns:
        Claude API adapter instance

    Raises:
        HTTPException: If plugin is not initialized
    """
    from fastapi import HTTPException

    if not proxy_service.plugin_registry:
        raise HTTPException(status_code=503, detail="Plugin manager not initialized")
    adapter = proxy_service.plugin_registry.get_adapter("claude_api")
    if not adapter:
        raise HTTPException(status_code=503, detail="Claude API plugin not initialized")
    return cast("ClaudeAPIAdapter", adapter)


def get_claude_api_detection_service(proxy_service: ProxyService) -> Any | None:
    """Get Claude API detection service.

    Args:
        proxy_service: Proxy service dependency

    Returns:
        Claude API detection service if available, None otherwise
    """
    if not proxy_service.plugin_registry:
        return None
    # Access PluginManager's internal registry
    from ccproxy.services.plugins import PluginManager

    if isinstance(proxy_service.plugin_registry, PluginManager):
        plugin = proxy_service.plugin_registry.plugin_registry.get_plugin("claude_api")
        if plugin and hasattr(plugin, "_detection_service"):
            return plugin._detection_service
    return None


def get_claude_sdk_adapter(proxy_service: ProxyService) -> Any:
    """Get Claude SDK adapter instance.

    Args:
        proxy_service: Proxy service dependency

    Returns:
        Claude SDK adapter instance

    Raises:
        HTTPException: If plugin is not initialized
    """
    from fastapi import HTTPException

    if not proxy_service.plugin_registry:
        raise HTTPException(status_code=503, detail="Plugin manager not initialized")
    adapter = proxy_service.plugin_registry.get_adapter("claude_sdk")
    if not adapter:
        raise HTTPException(status_code=503, detail="Claude SDK plugin not initialized")
    return adapter


def get_claude_sdk_detection_service(proxy_service: ProxyService) -> Any | None:
    """Get Claude SDK detection service.

    Args:
        proxy_service: Proxy service dependency

    Returns:
        Claude SDK detection service if available, None otherwise
    """
    if not proxy_service.plugin_registry:
        return None
    # Access PluginManager's internal registry
    from ccproxy.services.plugins import PluginManager

    if isinstance(proxy_service.plugin_registry, PluginManager):
        plugin = proxy_service.plugin_registry.plugin_registry.get_plugin("claude_sdk")
        if plugin and hasattr(plugin, "_detection_service"):
            return plugin._detection_service
    return None


def get_codex_adapter(proxy_service: ProxyService) -> CodexAdapter:
    """Get Codex adapter instance.

    Args:
        proxy_service: Proxy service dependency

    Returns:
        Codex adapter instance

    Raises:
        HTTPException: If plugin is not initialized
    """
    from fastapi import HTTPException

    if not proxy_service.plugin_registry:
        raise HTTPException(status_code=503, detail="Plugin manager not initialized")
    adapter = proxy_service.plugin_registry.get_adapter("codex")
    if not adapter:
        raise HTTPException(status_code=503, detail="Codex plugin not initialized")
    return cast("CodexAdapter", adapter)


def get_codex_detection_service(proxy_service: ProxyService) -> Any | None:
    """Get Codex detection service.

    Args:
        proxy_service: Proxy service dependency

    Returns:
        Codex detection service if available, None otherwise
    """
    if not proxy_service.plugin_registry:
        return None
    # Access PluginManager's internal registry
    from ccproxy.services.plugins import PluginManager

    if isinstance(proxy_service.plugin_registry, PluginManager):
        plugin = proxy_service.plugin_registry.plugin_registry.get_plugin("codex")
        if plugin and hasattr(plugin, "_detection_service"):
            return plugin._detection_service
    return None


# Type aliases for service dependencies
SettingsDep = Annotated[Settings, Depends(get_cached_settings)]
ProxyServiceDep = Annotated[ProxyService, Depends(get_proxy_service)]
HTTPClientDep = Annotated[httpx.AsyncClient, Depends(get_http_client)]
ObservabilityMetricsDep = Annotated[
    PrometheusMetrics, Depends(get_observability_metrics)
]
LogStorageDep = Annotated[SimpleDuckDBStorage | None, Depends(get_log_storage)]
DuckDBStorageDep = Annotated[SimpleDuckDBStorage | None, Depends(get_duckdb_storage)]

# Type aliases for plugin dependencies
ClaudeAPIAdapterDep = Annotated[
    "ClaudeAPIAdapter",
    Depends(lambda ps=Depends(get_proxy_service): get_claude_api_adapter(ps)),
]
ClaudeAPIDetectionDep = Annotated[
    Any | None,
    Depends(lambda ps=Depends(get_proxy_service): get_claude_api_detection_service(ps)),
]

ClaudeSDKAdapterDep = Annotated[
    Any, Depends(lambda ps=Depends(get_proxy_service): get_claude_sdk_adapter(ps))
]
ClaudeSDKDetectionDep = Annotated[
    Any | None,
    Depends(lambda ps=Depends(get_proxy_service): get_claude_sdk_detection_service(ps)),
]

CodexAdapterDep = Annotated[
    "CodexAdapter", Depends(lambda ps=Depends(get_proxy_service): get_codex_adapter(ps))
]
CodexDetectionDep = Annotated[
    Any | None,
    Depends(lambda ps=Depends(get_proxy_service): get_codex_detection_service(ps)),
]
