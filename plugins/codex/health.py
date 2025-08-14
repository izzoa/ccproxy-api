"""Codex health check implementation."""

import structlog

from ccproxy.plugins.protocol import HealthCheckResult

from .config import CodexSettings


logger = structlog.get_logger(__name__)


async def codex_health_check(config: CodexSettings | None) -> HealthCheckResult:
    """Perform health check for Codex plugin."""
    try:
        if not config:
            return HealthCheckResult(
                status="fail",
                componentId="plugin-codex",
                output="Codex plugin configuration not available",
                version="1.0.0",
            )

        # Check basic configuration validity
        if not config.base_url:
            return HealthCheckResult(
                status="fail",
                componentId="plugin-codex",
                output="Codex base URL not configured",
                version="1.0.0",
            )

        # Check OAuth configuration
        if not config.oauth.base_url or not config.oauth.client_id:
            return HealthCheckResult(
                status="warn",
                componentId="plugin-codex",
                output="Codex OAuth configuration incomplete",
                version="1.0.0",
            )

        # Basic health check passes
        return HealthCheckResult(
            status="pass",
            componentId="plugin-codex",
            output="Codex plugin is healthy",
            version="1.0.0",
            details={
                "base_url": config.base_url,
                "oauth_configured": bool(
                    config.oauth.base_url and config.oauth.client_id
                ),
                "verbose_logging": config.verbose_logging,
            },
        )

    except Exception as e:
        logger.error("codex_health_check_failed", error=str(e))
        return HealthCheckResult(
            status="fail",
            componentId="plugin-codex",
            output=f"Codex health check failed: {str(e)}",
            version="1.0.0",
        )
