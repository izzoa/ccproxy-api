"""Dynamic model fetcher for LiteLLM model metadata."""

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import httpx

from ccproxy.core.logging import get_logger
from ccproxy.models.provider import ModelCard


logger = get_logger(__name__)

DEFAULT_LITELLM_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"


class ModelFetcher:
    """Fetches and converts model metadata from LiteLLM format to internal ModelCard format."""

    def __init__(
        self,
        source_url: str = DEFAULT_LITELLM_URL,
        cache_dir: Path | None = None,
        cache_ttl_hours: int = 24,
        timeout: int = 30,
    ):
        """Initialize model fetcher.

        Args:
            source_url: URL to download model metadata from
            cache_dir: Directory for caching fetched data
            cache_ttl_hours: Hours before cache expires
            timeout: Request timeout in seconds
        """
        self.source_url = source_url
        self.cache_dir = cache_dir
        self.cache_ttl_hours = cache_ttl_hours
        self.timeout = timeout
        self._memory_cache: dict[str, Any] | None = None
        self._memory_cache_time: datetime | None = None

    def _get_cache_path(self) -> Path | None:
        """Get the cache file path."""
        if self.cache_dir is None:
            return None
        cache_file = self.cache_dir / "litellm_models.json"
        return cache_file

    def _is_cache_valid(self, cache_path: Path) -> bool:
        """Check if cache file exists and is still valid."""
        if not cache_path.exists():
            return False

        try:
            stat = cache_path.stat()
            cache_age = datetime.now(UTC) - datetime.fromtimestamp(
                stat.st_mtime, tz=UTC
            )
            return cache_age < timedelta(hours=self.cache_ttl_hours)
        except OSError:
            return False

    def _load_from_cache(self) -> dict[str, Any] | None:
        """Load model data from cache file."""
        cache_path = self._get_cache_path()
        if cache_path is None:
            return None

        if not self._is_cache_valid(cache_path):
            return None

        try:
            with cache_path.open("r") as f:
                data = json.load(f)
                logger.debug("loaded_models_from_cache", cache_path=str(cache_path))
                return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("cache_load_failed", error=str(e))
            return None

    def _save_to_cache(self, data: dict[str, Any]) -> None:
        """Save model data to cache file."""
        cache_path = self._get_cache_path()
        if cache_path is None:
            return

        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with cache_path.open("w") as f:
                json.dump(data, f)
            logger.debug("saved_models_to_cache", cache_path=str(cache_path))
        except OSError as e:
            logger.warning("cache_save_failed", error=str(e))

    async def _fetch_from_url(self) -> dict[str, Any] | None:
        """Fetch model data from remote URL."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(self.source_url)
                response.raise_for_status()
                data = response.json()
                logger.info(
                    "fetched_models_from_url",
                    url=self.source_url,
                    model_count=len(data),
                )
                return data
        except httpx.HTTPError as e:
            logger.error("model_fetch_http_error", error=str(e), url=self.source_url)
            return None
        except json.JSONDecodeError as e:
            logger.error("model_fetch_json_error", error=str(e), url=self.source_url)
            return None
        except Exception as e:
            logger.error(
                "model_fetch_unexpected_error", error=str(e), url=self.source_url
            )
            return None

    async def fetch_all_models(self, use_cache: bool = True) -> dict[str, Any] | None:
        """Fetch all models from cache or remote source.

        Args:
            use_cache: Whether to use cached data if available

        Returns:
            Dictionary of model metadata in LiteLLM format, or None if fetch fails
        """
        if use_cache and self._memory_cache is not None and self._memory_cache_time:
            cache_age = datetime.now(UTC) - self._memory_cache_time
            if cache_age < timedelta(hours=1):
                logger.debug("using_memory_cache")
                return self._memory_cache

        if use_cache:
            cached_data = self._load_from_cache()
            if cached_data is not None:
                self._memory_cache = cached_data
                self._memory_cache_time = datetime.now(UTC)
                return cached_data

        data = await self._fetch_from_url()
        if data is not None:
            self._save_to_cache(data)
            self._memory_cache = data
            self._memory_cache_time = datetime.now(UTC)

        return data

    def _convert_to_model_card(
        self, model_id: str, model_data: dict[str, Any]
    ) -> ModelCard | None:
        """Convert LiteLLM model data to ModelCard.

        Args:
            model_id: Model identifier
            model_data: Model metadata from LiteLLM

        Returns:
            ModelCard instance or None if conversion fails
        """
        try:
            litellm_provider = model_data.get("litellm_provider")
            if litellm_provider == "anthropic":
                owned_by = "anthropic"
            elif litellm_provider == "openai":
                owned_by = "openai"
            else:
                owned_by = litellm_provider

            card = ModelCard(
                id=model_id,
                object="model",
                owned_by=owned_by,
                max_input_tokens=model_data.get("max_input_tokens"),
                max_output_tokens=model_data.get("max_output_tokens"),
                max_tokens=model_data.get("max_tokens"),
                supports_vision=model_data.get("supports_vision"),
                supports_function_calling=model_data.get("supports_function_calling"),
                supports_parallel_function_calling=model_data.get(
                    "supports_parallel_function_calling"
                ),
                supports_tool_choice=model_data.get("supports_tool_choice"),
                supports_response_schema=model_data.get("supports_response_schema"),
                supports_prompt_caching=model_data.get("supports_prompt_caching"),
                supports_system_messages=model_data.get("supports_system_messages"),
                supports_assistant_prefill=model_data.get("supports_assistant_prefill"),
                supports_computer_use=model_data.get("supports_computer_use"),
                supports_pdf_input=model_data.get("supports_pdf_input"),
                supports_reasoning=model_data.get("supports_reasoning"),
                mode=model_data.get("mode"),
                litellm_provider=litellm_provider,
                deprecation_date=model_data.get("deprecation_date"),
            )
            return card
        except Exception as e:
            logger.warning(
                "model_card_conversion_failed", model_id=model_id, error=str(e)
            )
            return None

    async def fetch_models_by_provider(
        self,
        provider: Literal["anthropic", "openai", "all"] = "all",
        use_cache: bool = True,
    ) -> list[ModelCard]:
        """Fetch models filtered by provider.

        Args:
            provider: Provider to filter by ('anthropic', 'openai', or 'all')
            use_cache: Whether to use cached data if available

        Returns:
            List of ModelCard instances
        """
        all_models = await self.fetch_all_models(use_cache=use_cache)
        if all_models is None:
            logger.warning("model_fetch_failed", provider=provider)
            return []

        model_cards: list[ModelCard] = []

        for model_id, model_data in all_models.items():
            if not isinstance(model_data, dict):
                continue

            litellm_provider = model_data.get("litellm_provider")
            mode = model_data.get("mode", "chat")

            if mode != "chat":
                continue

            if (
                provider == "anthropic"
                and litellm_provider != "anthropic"
                or provider == "openai"
                and litellm_provider != "openai"
            ):
                continue

            card = self._convert_to_model_card(model_id, model_data)
            if card is not None:
                model_cards.append(card)

        logger.info(
            "models_fetched_by_provider",
            provider=provider,
            model_count=len(model_cards),
        )
        return model_cards
