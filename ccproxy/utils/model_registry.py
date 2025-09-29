"""Centralized model registry for metadata management."""

import asyncio
from datetime import datetime, timedelta
from typing import Literal

from ccproxy.core.logging import get_logger
from ccproxy.models.provider import ModelCard
from ccproxy.utils.model_fetcher import ModelFetcher


logger = get_logger(__name__)


class ModelRegistry:
    """Centralized registry for model metadata with auto-refresh."""

    def __init__(
        self,
        fetcher: ModelFetcher | None = None,
        refresh_interval_hours: float = 6.0,
    ):
        """Initialize model registry.

        Args:
            fetcher: ModelFetcher instance (creates default if None)
            refresh_interval_hours: Hours between auto-refresh
        """
        self.fetcher = fetcher or ModelFetcher()
        self.refresh_interval_hours = refresh_interval_hours
        self._models_by_provider: dict[str, dict[str, ModelCard]] = {}
        self._last_refresh: dict[str, datetime] = {}
        self._lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize registry with initial model fetch."""
        if self._initialized:
            return

        async with self._lock:
            if self._initialized:
                return

            logger.info("initializing_model_registry")
            await self._fetch_all_providers()
            self._initialized = True
            logger.info(
                "model_registry_initialized",
                anthropic_models=len(self._models_by_provider.get("anthropic", {})),
                openai_models=len(self._models_by_provider.get("openai", {})),
            )

    async def _fetch_all_providers(self) -> None:
        """Fetch models for all providers."""
        for provider in ["anthropic", "openai"]:  # type: ignore[assignment]
            try:
                models = await self.fetcher.fetch_models_by_provider(
                    provider=provider, use_cache=True
                )
                self._models_by_provider[provider] = {
                    model.id: model for model in models
                }
                self._last_refresh[provider] = datetime.now()
                logger.debug(
                    "provider_models_fetched",
                    provider=provider,
                    model_count=len(models),
                )
            except Exception as e:
                logger.error(
                    "provider_fetch_failed", provider=provider, error=str(e), exc_info=e
                )
                self._models_by_provider[provider] = {}

    async def _should_refresh(self, provider: str) -> bool:
        """Check if provider models should be refreshed."""
        last_refresh = self._last_refresh.get(provider)
        if last_refresh is None:
            return True

        age = datetime.now() - last_refresh
        return age > timedelta(hours=self.refresh_interval_hours)

    async def _refresh_provider(self, provider: str) -> None:
        """Refresh models for a specific provider."""
        try:
            models = await self.fetcher.fetch_models_by_provider(
                provider=provider, use_cache=False  # type: ignore[arg-type]
            )
            async with self._lock:
                self._models_by_provider[provider] = {
                    model.id: model for model in models
                }
                self._last_refresh[provider] = datetime.now()
            logger.info(
                "provider_models_refreshed",
                provider=provider,
                model_count=len(models),
            )
        except Exception as e:
            logger.error(
                "provider_refresh_failed", provider=provider, error=str(e), exc_info=e
            )

    async def get_model(
        self, model_id: str, provider: Literal["anthropic", "openai"] | None = None
    ) -> ModelCard | None:
        """Get model metadata by ID.

        Args:
            model_id: Model identifier
            provider: Provider name (if known). Will search all if None.

        Returns:
            ModelCard if found, None otherwise
        """
        if not self._initialized:
            await self.initialize()

        if provider:
            if await self._should_refresh(provider):
                await self._refresh_provider(provider)

            return self._models_by_provider.get(provider, {}).get(model_id)

        for provider_name, models in self._models_by_provider.items():
            if model_id in models:
                if await self._should_refresh(provider_name):
                    await self._refresh_provider(provider_name)
                    models = self._models_by_provider.get(provider_name, {})
                return models.get(model_id)

        return None

    async def get_all_models(
        self, provider: Literal["anthropic", "openai"] | None = None
    ) -> list[ModelCard]:
        """Get all models for a provider or all providers.

        Args:
            provider: Provider name, or None for all providers

        Returns:
            List of ModelCard objects
        """
        if not self._initialized:
            await self.initialize()

        if provider:
            if await self._should_refresh(provider):
                await self._refresh_provider(provider)
            return list(self._models_by_provider.get(provider, {}).values())

        all_models: list[ModelCard] = []
        for provider_name in self._models_by_provider:
            if await self._should_refresh(provider_name):
                await self._refresh_provider(provider_name)
        for models in self._models_by_provider.values():
            all_models.extend(models.values())
        return all_models

    async def refresh_all(self) -> None:
        """Force refresh all provider models."""
        logger.info("refreshing_all_models")
        await self._fetch_all_providers()

    def get_stats(self) -> dict[str, int]:
        """Get registry statistics.

        Returns:
            Dict with model counts by provider
        """
        return {
            provider: len(models)
            for provider, models in self._models_by_provider.items()
        }


_global_registry: ModelRegistry | None = None


def get_model_registry() -> ModelRegistry:
    """Get the global model registry singleton.

    Returns:
        Global ModelRegistry instance
    """
    global _global_registry
    if _global_registry is None:
        _global_registry = ModelRegistry()
    return _global_registry


def set_model_registry(registry: ModelRegistry) -> None:
    """Set the global model registry (for testing).

    Args:
        registry: ModelRegistry instance to use globally
    """
    global _global_registry
    _global_registry = registry
