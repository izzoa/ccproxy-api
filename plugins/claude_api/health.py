"""Claude API plugin health check implementation."""

from typing import Any, Literal

import structlog

from ccproxy.plugins.protocol import HealthCheckResult
from ccproxy.services.credentials.manager import CredentialsManager

from .config import ClaudeAPISettings


logger = structlog.get_logger(__name__)


async def claude_api_health_check(
    config: ClaudeAPISettings | None,
    credentials_manager: CredentialsManager | None = None,
) -> HealthCheckResult:
    """Perform health check for Claude API plugin.
    
    Args:
        config: Plugin configuration
        credentials_manager: Credentials manager for OAuth token status
        
    Returns:
        HealthCheckResult with plugin status including OAuth token details
    """
    try:
        if not config:
            return HealthCheckResult(
                status="fail",
                componentId="plugin-claude-api",
                componentType="provider_plugin",
                output="Claude API plugin configuration not available",
                version="1.0.0",
            )
        
        # Check if plugin is enabled
        if not config.enabled:
            return HealthCheckResult(
                status="warn",
                componentId="plugin-claude-api",
                componentType="provider_plugin",
                output="Claude API plugin is disabled",
                version="1.0.0",
                details={"enabled": False},
            )
        
        # Check basic configuration
        if not config.base_url:
            return HealthCheckResult(
                status="fail",
                componentId="plugin-claude-api",
                componentType="provider_plugin",
                output="Claude API base URL not configured",
                version="1.0.0",
            )
        
        # Get authentication status from credentials manager
        auth_details: dict[str, Any] = {}
        if credentials_manager:
            try:
                # Use the new helper method to get auth status
                auth_details = await credentials_manager.get_auth_status()
            except Exception as e:
                logger.debug("Failed to check auth status", error=str(e))
                auth_details = {
                    "auth_configured": False,
                    "auth_error": str(e),
                }
        else:
            auth_details = {
                "auth_configured": False,
                "auth_status": "Credentials manager not available",
            }
        
        # Determine overall status and build output message
        status: Literal["pass", "warn", "fail"]
        output_parts = []
        
        if auth_details.get("token_available") and not auth_details.get("token_expired"):
            output_parts.append("Authenticated")
            if auth_details.get("subscription_type"):
                output_parts.append(f"Subscription: {auth_details['subscription_type']}")
            if auth_details.get("has_claude_max"):
                output_parts.append("Claude Max")
            elif auth_details.get("has_claude_pro"):
                output_parts.append("Claude Pro")
            status = "pass"
        elif auth_details.get("token_expired"):
            output_parts.append("Token expired")
            status = "warn"
        elif auth_details.get("auth_configured"):
            output_parts.append("Auth configured but token unavailable")
            status = "warn"
        else:
            output_parts.append("Authentication not configured")
            status = "warn"
        
        # Add model info
        if config.models:
            output_parts.append(f"{len(config.models)} models available")
        
        output = "Claude API: " + ", ".join(output_parts)
        
        # Build details dict with non-sensitive information
        details = {
            "base_url": config.base_url,
            "enabled": config.enabled,
            "model_count": len(config.models) if config.models else 0,
            "support_openai_format": config.support_openai_format,
            **auth_details,  # Include all auth details from helper
        }
        
        return HealthCheckResult(
            status=status,
            componentId="plugin-claude-api",
            componentType="provider_plugin",
            output=output,
            version="1.0.0",
            details=details,
        )
        
    except Exception as e:
        logger.error("claude_api_health_check_failed", error=str(e))
        return HealthCheckResult(
            status="fail",
            componentId="plugin-claude-api",
            componentType="provider_plugin",
            output=f"Claude API health check failed: {str(e)}",
            version="1.0.0",
        )