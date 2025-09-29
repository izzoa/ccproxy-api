"""Claude API plugin configuration."""

from pathlib import Path

from pydantic import Field

from ccproxy.core.system import get_xdg_cache_home
from ccproxy.models.provider import ModelCard, ModelMappingRule, ProviderConfig
from ccproxy.plugins.claude_shared.model_defaults import (
    DEFAULT_CLAUDE_MODEL_CARDS,
    DEFAULT_CLAUDE_MODEL_MAPPINGS,
)


class ClaudeAPISettings(ProviderConfig):
    """Claude API specific configuration.

    This configuration extends the base ProviderConfig to include
    Claude API specific settings like API endpoint and model support.
    """

    # Base configuration from ProviderConfig
    name: str = "claude-api"
    base_url: str = "https://api.anthropic.com"
    supports_streaming: bool = True
    requires_auth: bool = True
    auth_type: str = "oauth"

    # Claude API specific settings
    enabled: bool = True
    priority: int = 5  # Higher priority than SDK-based approach
    default_max_tokens: int = 4096

    model_mappings: list[ModelMappingRule] = Field(
        default_factory=lambda: [
            rule.model_copy(deep=True) for rule in DEFAULT_CLAUDE_MODEL_MAPPINGS
        ]
    )
    models_endpoint: list[ModelCard] = Field(
        default_factory=lambda: [
            card.model_copy(deep=True) for card in DEFAULT_CLAUDE_MODEL_CARDS
        ]
    )

    # Feature flags
    include_sdk_content_as_xml: bool = False
    support_openai_format: bool = True  # Support both Anthropic and OpenAI formats

    # System prompt injection mode
    system_prompt_injection_mode: str = "minimal"  # "none", "minimal", or "full"

    # NEW: Auth manager override support
    auth_manager: str | None = (
        None  # Override auth manager name (e.g., 'oauth_claude_lb' for load balancing)
    )

    # Dynamic model fetching configuration
    dynamic_models_enabled: bool = Field(
        default=True,
        description="Enable dynamic model fetching from LiteLLM",
    )
    models_source_url: str = Field(
        default="https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json",
        description="URL to fetch model metadata from",
    )
    models_cache_dir: Path = Field(
        default_factory=lambda: get_xdg_cache_home() / "ccproxy" / "models",
        description="Directory for caching model metadata",
    )
    models_cache_ttl_hours: int = Field(
        default=24,
        ge=1,
        le=168,
        description="Hours before model cache expires",
    )
    models_fetch_timeout: int = Field(
        default=30,
        ge=1,
        le=300,
        description="Timeout in seconds for fetching model metadata",
    )

    # Model validation configuration
    validate_token_limits: bool = Field(
        default=True,
        description="Enforce token limits based on model metadata",
    )
    enforce_capabilities: bool = Field(
        default=True,
        description="Enforce model capabilities (vision, function calling, etc.)",
    )
    warn_on_limits: bool = Field(
        default=True,
        description="Add warning headers when approaching token limits",
    )
    warn_threshold: float = Field(
        default=0.9,
        ge=0.5,
        le=1.0,
        description="Token usage threshold (0.0-1.0) to trigger warnings",
    )
