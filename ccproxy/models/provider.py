"""Provider configuration models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ModelMappingRule(BaseModel):
    """Ordered mapping rule for translating client model identifiers."""

    match: str = Field(..., description="Client-facing model identifier or pattern")
    target: str = Field(..., description="Upstream model identifier to use")
    kind: Literal["exact", "regex", "prefix", "suffix"] = Field(
        default="exact",
        description="Type of match to apply for this rule",
    )
    flags: list[Literal["IGNORECASE"]] = Field(
        default_factory=list,
        description="Optional regex flags to apply when kind=regex",
    )
    notes: str | None = Field(
        default=None,
        description="Optional human-readable description of this rule",
    )


class ModelCard(BaseModel):
    """Representation of a model entry returned by /models."""

    id: str = Field(..., description="Unique model identifier exposed to clients")
    object: str = Field(default="model", description="OpenAI-compatible object type")
    created: int | None = Field(
        default=None, description="Unix timestamp when the model became available"
    )
    owned_by: str | None = Field(
        default=None, description="Provider or organization that owns the model"
    )
    permission: list[dict[str, Any]] | None = Field(
        default=None,
        description="Optional list of permission descriptors",
    )
    root: str | None = Field(
        default=None, description="Root model identifier for fine-tuned variants"
    )
    parent: str | None = Field(
        default=None, description="Parent model identifier, if any"
    )
    max_input_tokens: int | None = Field(
        default=None, description="Maximum input tokens supported by the model"
    )
    max_output_tokens: int | None = Field(
        default=None, description="Maximum output tokens supported by the model"
    )
    max_tokens: int | None = Field(
        default=None, description="Maximum total tokens (legacy field)"
    )
    supports_vision: bool | None = Field(
        default=None, description="Whether the model supports vision/image inputs"
    )
    supports_function_calling: bool | None = Field(
        default=None, description="Whether the model supports function calling"
    )
    supports_parallel_function_calling: bool | None = Field(
        default=None, description="Whether the model supports parallel function calling"
    )
    supports_tool_choice: bool | None = Field(
        default=None, description="Whether the model supports tool choice"
    )
    supports_response_schema: bool | None = Field(
        default=None,
        description="Whether the model supports response schema (structured output)",
    )
    supports_prompt_caching: bool | None = Field(
        default=None, description="Whether the model supports prompt caching"
    )
    supports_system_messages: bool | None = Field(
        default=None, description="Whether the model supports system messages"
    )
    supports_assistant_prefill: bool | None = Field(
        default=None, description="Whether the model supports assistant prefill"
    )
    supports_computer_use: bool | None = Field(
        default=None, description="Whether the model supports computer use tools"
    )
    supports_pdf_input: bool | None = Field(
        default=None, description="Whether the model supports PDF input"
    )
    supports_reasoning: bool | None = Field(
        default=None, description="Whether the model supports reasoning mode"
    )
    mode: str | None = Field(
        default=None,
        description="Model mode (e.g., 'chat', 'completion', 'image_generation')",
    )
    litellm_provider: str | None = Field(
        default=None, description="Provider identifier from LiteLLM"
    )
    deprecation_date: str | None = Field(
        default=None, description="Date when the model will be deprecated (YYYY-MM-DD)"
    )

    model_config = ConfigDict(extra="allow")


class ProviderConfig(BaseModel):
    """Configuration for a provider plugin."""

    name: str = Field(..., description="Provider name")
    base_url: str = Field(..., description="Base URL for the provider API")
    supports_streaming: bool = Field(
        default=False, description="Whether the provider supports streaming"
    )
    requires_auth: bool = Field(
        default=True, description="Whether the provider requires authentication"
    )
    auth_type: str | None = Field(
        default=None, description="Authentication type (bearer, api_key, etc.)"
    )
    model_mappings: list[ModelMappingRule] = Field(
        default_factory=list,
        description="Ordered list of client→upstream model mapping rules",
    )
    models_endpoint: list[ModelCard] = Field(
        default_factory=list,
        description="Model metadata returned by the provider's /models endpoint",
    )
