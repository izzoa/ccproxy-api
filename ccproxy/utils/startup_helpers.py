"""Startup utility functions for application lifecycle management.

This module contains simple utility functions to extract and organize
the complex startup logic from the main lifespan function, following
the KISS principle and avoiding overengineering.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from fastapi import FastAPI

from ccproxy.auth.exceptions import CredentialsNotFoundError
from ccproxy.observability import get_metrics

# Note: get_claude_cli_info is imported locally to avoid circular imports
from ccproxy.observability.storage.duckdb_simple import SimpleDuckDBStorage
from ccproxy.scheduler.errors import SchedulerError
from ccproxy.scheduler.manager import start_scheduler, stop_scheduler
from ccproxy.services.credentials.manager import CredentialsManager


# Note: get_permission_service is imported locally to avoid circular imports

if TYPE_CHECKING:
    from ccproxy.config.settings import Settings

logger = structlog.get_logger(__name__)


async def validate_claude_authentication_startup(
    app: FastAPI, settings: Settings
) -> None:
    """Validate Claude authentication credentials at startup.

    Args:
        app: FastAPI application instance
        settings: Application settings
    """
    try:
        credentials_manager = CredentialsManager()
        validation = await credentials_manager.validate()

        if validation.valid and not validation.expired:
            credentials = validation.credentials
            oauth_token = credentials.claude_ai_oauth if credentials else None

            if oauth_token and oauth_token.expires_at_datetime:
                hours_until_expiry = int(
                    (
                        oauth_token.expires_at_datetime - datetime.now(UTC)
                    ).total_seconds()
                    / 3600
                )
                logger.debug(
                    "claude_token_valid",
                    expires_in_hours=hours_until_expiry,
                    subscription_type=oauth_token.subscription_type,
                    credentials_path=str(validation.path) if validation.path else None,
                )
            else:
                logger.debug(
                    "claude_token_valid", credentials_path=str(validation.path)
                )
        elif validation.expired:
            logger.warning(
                "claude_token_expired",
                message="Claude authentication token has expired. Please run 'ccproxy auth login' to refresh.",
                credentials_path=str(validation.path) if validation.path else None,
            )
        else:
            logger.warning(
                "claude_token_invalid",
                message="Claude authentication token is invalid. Please run 'ccproxy auth login'.",
                credentials_path=str(validation.path) if validation.path else None,
            )
    except CredentialsNotFoundError:
        logger.warning(
            "claude_token_not_found",
            message="No Claude authentication credentials found. Please run 'ccproxy auth login' to authenticate.",
            searched_paths=settings.auth.storage.storage_paths,
        )
    except Exception as e:
        logger.error(
            "claude_token_validation_error",
            error=str(e),
            message="Failed to validate Claude authentication token. The server will continue without Claude authentication.",
            exc_info=True,
        )


async def check_version_updates_startup(app: FastAPI, settings: Settings) -> None:
    """Trigger version update check at startup.

    Manually runs the version check task once during application startup,
    before the scheduler starts managing periodic checks.

    Args:
        app: FastAPI application instance
        settings: Application settings
    """
    # Skip version check if disabled by settings
    if not settings.scheduler.version_check_enabled:
        logger.debug("version_check_startup_disabled")
        return

    try:
        # Import locally to avoid circular imports and create task instance
        from ccproxy.scheduler.tasks import VersionUpdateCheckTask

        # Create a temporary task instance for startup check
        version_task = VersionUpdateCheckTask(
            name="version_check_startup",
            interval_seconds=settings.scheduler.version_check_interval_hours * 3600,
            enabled=True,
            version_check_cache_ttl_hours=settings.scheduler.version_check_cache_ttl_hours,
            skip_first_scheduled_run=False,
        )

        # Run the version check once and wait for it to complete
        success = await version_task.run()

        if success:
            logger.debug("version_check_startup_completed")
        else:
            logger.debug("version_check_startup_failed")

    except Exception as e:
        logger.debug(
            "version_check_startup_error",
            error=str(e),
            error_type=type(e).__name__,
        )


async def check_claude_cli_startup(app: FastAPI, settings: Settings) -> None:
    """Check Claude CLI availability at startup.

    Note: The plugin will handle Claude CLI detection and validation.

    Args:
        app: FastAPI application instance
        settings: Application settings
    """
    # Claude CLI check is now handled by the plugin
    pass


async def initialize_log_storage_startup(app: FastAPI, settings: Settings) -> None:
    """Initialize log storage if needed and backend is DuckDB.

    Args:
        app: FastAPI application instance
        settings: Application settings
    """
    if (
        settings.observability.needs_storage_backend
        and settings.observability.log_storage_backend == "duckdb"
    ):
        try:
            storage = SimpleDuckDBStorage(
                database_path=settings.observability.duckdb_path
            )
            await storage.initialize()
            app.state.log_storage = storage
            logger.debug(
                "log_storage_initialized",
                backend="duckdb",
                path=str(settings.observability.duckdb_path),
                collection_enabled=settings.observability.logs_collection_enabled,
            )
        except Exception as e:
            logger.error("log_storage_initialization_failed", error=str(e))
            # Continue without log storage (graceful degradation)


async def initialize_log_storage_shutdown(app: FastAPI) -> None:
    """Close log storage if initialized.

    Args:
        app: FastAPI application instance
    """
    if hasattr(app.state, "log_storage") and app.state.log_storage:
        try:
            await app.state.log_storage.close()
            logger.debug("log_storage_closed")
        except Exception as e:
            logger.error("log_storage_close_failed", error=str(e))


async def setup_scheduler_startup(app: FastAPI, settings: Settings) -> None:
    """Start scheduler system and configure tasks.

    Args:
        app: FastAPI application instance
        settings: Application settings
    """
    try:
        scheduler = await start_scheduler(settings)
        app.state.scheduler = scheduler
        logger.debug("scheduler_initialized")

        # Add session pool stats task if session manager is available
        if (
            scheduler
            and hasattr(app.state, "session_manager")
            and app.state.session_manager
        ):
            try:
                # Add session pool stats task that runs every minute
                await scheduler.add_task(
                    task_name="session_pool_stats",
                    task_type="pool_stats",
                    interval_seconds=60,  # Every minute
                    enabled=True,
                    pool_manager=app.state.session_manager,
                )
                logger.debug("session_pool_stats_task_added", interval_seconds=60)
            except Exception as e:
                logger.error(
                    "session_pool_stats_task_add_failed",
                    error=str(e),
                    error_type=type(e).__name__,
                )
    except SchedulerError as e:
        logger.error("scheduler_initialization_failed", error=str(e))
        # Continue startup even if scheduler fails (graceful degradation)


async def setup_scheduler_shutdown(app: FastAPI) -> None:
    """Stop scheduler system.

    Args:
        app: FastAPI application instance
    """
    try:
        scheduler = getattr(app.state, "scheduler", None)
        await stop_scheduler(scheduler)
        logger.debug("scheduler_stopped_lifespan")
    except SchedulerError as e:
        logger.error("scheduler_stop_failed", error=str(e))


async def setup_session_manager_shutdown(app: FastAPI) -> None:
    """Shutdown Claude SDK session manager if it was created.

    Args:
        app: FastAPI application instance
    """
    if hasattr(app.state, "session_manager") and app.state.session_manager:
        try:
            await app.state.session_manager.shutdown()
            logger.debug("claude_sdk_session_manager_shutdown")
        except Exception as e:
            logger.error("claude_sdk_session_manager_shutdown_failed", error=str(e))


async def initialize_proxy_service_startup(app: FastAPI, settings: Settings) -> None:
    """Initialize ProxyService and store in app state.

    Args:
        app: FastAPI application instance
        settings: Application settings
    """
    try:
        # Create HTTP client for proxy
        from ccproxy.core.http import BaseProxyClient, HTTPXClient
        from ccproxy.services.container import ServiceContainer

        http_client = HTTPXClient()
        proxy_client = BaseProxyClient(http_client)

        # Get global metrics instance
        metrics = get_metrics()

        # Create credentials manager
        credentials_manager = CredentialsManager(config=settings.auth)

        # Create ServiceContainer and use it to create ProxyService
        container = ServiceContainer(settings)
        proxy_service = container.create_proxy_service(
            proxy_client=proxy_client,
            credentials_manager=credentials_manager,
            metrics=metrics,
        )

        # Initialize plugins
        scheduler = getattr(app.state, "scheduler", None)
        await proxy_service.initialize_plugins(scheduler)

        # Store in app state for reuse in dependencies
        app.state.proxy_service = proxy_service
        logger.debug("proxy_service_initialized")
    except Exception as e:
        logger.error("proxy_service_initialization_failed", error=str(e))
        # Continue startup even if ProxyService fails (graceful degradation)


async def initialize_permission_service_startup(
    app: FastAPI, settings: Settings
) -> None:
    """Initialize permission service.

    Note: The plugin will handle builtin_permissions configuration.

    Args:
        app: FastAPI application instance
        settings: Application settings
    """
    # Permission service initialization is now handled by the plugin
    # The plugin will check its own builtin_permissions setting
    pass


async def setup_permission_service_shutdown(app: FastAPI, settings: Settings) -> None:
    """Stop permission service (if it was initialized).

    Note: The plugin will handle permission service cleanup.

    Args:
        app: FastAPI application instance
        settings: Application settings
    """
    # Permission service cleanup is now handled by the plugin
    pass


async def flush_streaming_batches_shutdown(app: FastAPI) -> None:
    """Flush any remaining streaming log batches.

    Args:
        app: FastAPI application instance
    """
    try:
        from ccproxy.utils.simple_request_logger import flush_all_streaming_batches

        await flush_all_streaming_batches()
        logger.debug("streaming_batches_flushed")
    except Exception as e:
        logger.error("streaming_batches_flush_failed", error=str(e))
