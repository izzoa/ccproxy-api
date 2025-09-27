"""Default model metadata and mapping rules for the Codex provider."""

from __future__ import annotations

from ccproxy.models.provider import ModelCard, ModelMappingRule


DEFAULT_CODEX_MODEL_CARDS: list[ModelCard] = [
    ModelCard(
        id="gpt-5",
        created=1704000000,
        owned_by="openai",
        permission=[],
        root="gpt-5",
        parent=None,
    ),
    ModelCard(
        id="gpt-5-2025-08-07",
        created=1704000000,
        owned_by="openai",
        permission=[],
        root="gpt-5-2025-08-07",
        parent=None,
    ),
    ModelCard(
        id="gpt-5-mini",
        created=1704000000,
        owned_by="openai",
        permission=[],
        root="gpt-5-mini",
        parent=None,
    ),
    ModelCard(
        id="gpt-5-mini-2025-08-07",
        created=1704000000,
        owned_by="openai",
        permission=[],
        root="gpt-5-mini-2025-08-07",
        parent=None,
    ),
    ModelCard(
        id="gpt-5-nano",
        created=1704000000,
        owned_by="openai",
        permission=[],
        root="gpt-5-nano",
        parent=None,
    ),
    ModelCard(
        id="gpt-5-nano-2025-08-07",
        created=1704000000,
        owned_by="openai",
        permission=[],
        root="gpt-5-nano-2025-08-07",
        parent=None,
    ),
]


DEFAULT_CODEX_MODEL_MAPPINGS: list[ModelMappingRule] = [
    ModelMappingRule(match="gpt-5-nano", target="gpt-5"),
    ModelMappingRule(match="gpt-5-nano-2025-08-07", target="gpt-5"),
    ModelMappingRule(match="o3-mini", target="gpt-5"),
    ModelMappingRule(match="gpt-4.1", target="gpt-5"),
]


__all__ = [
    "DEFAULT_CODEX_MODEL_CARDS",
    "DEFAULT_CODEX_MODEL_MAPPINGS",
]
