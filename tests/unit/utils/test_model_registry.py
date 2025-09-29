import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from ccproxy.models.provider import ModelCard
from ccproxy.utils.model_fetcher import ModelFetcher
from ccproxy.utils.model_registry import ModelRegistry


@pytest.fixture
def mock_fetcher():
    fetcher = MagicMock(spec=ModelFetcher)
    fetcher.fetch_models_by_provider = AsyncMock()
    return fetcher


@pytest.fixture
def sample_model_cards():
    return [
        ModelCard(
            id="claude-3-5-sonnet-20241022",
            object="model",
            owned_by="anthropic",
            max_input_tokens=200000,
            max_output_tokens=8192,
            supports_vision=True,
            supports_function_calling=True,
        ),
        ModelCard(
            id="gpt-4",
            object="model",
            owned_by="openai",
            max_input_tokens=8192,
            max_output_tokens=4096,
            supports_vision=False,
            supports_function_calling=True,
        ),
    ]


@pytest.mark.asyncio
async def test_initialize_registry(mock_fetcher, sample_model_cards):
    mock_fetcher.fetch_models_by_provider.return_value = [sample_model_cards[0]]

    registry = ModelRegistry(fetcher=mock_fetcher, refresh_interval_hours=1.0)
    await registry.initialize()

    assert registry._initialized is True
    assert mock_fetcher.fetch_models_by_provider.call_count == 2


@pytest.mark.asyncio
async def test_get_model_by_id(mock_fetcher, sample_model_cards):
    mock_fetcher.fetch_models_by_provider.return_value = [sample_model_cards[0]]

    registry = ModelRegistry(fetcher=mock_fetcher, refresh_interval_hours=1.0)
    await registry.initialize()

    model = await registry.get_model("claude-3-5-sonnet-20241022", provider="anthropic")

    assert model is not None
    assert model.id == "claude-3-5-sonnet-20241022"
    assert model.max_input_tokens == 200000


@pytest.mark.asyncio
async def test_get_model_not_found(mock_fetcher, sample_model_cards):
    mock_fetcher.fetch_models_by_provider.return_value = [sample_model_cards[0]]

    registry = ModelRegistry(fetcher=mock_fetcher, refresh_interval_hours=1.0)
    await registry.initialize()

    model = await registry.get_model("nonexistent-model", provider="anthropic")

    assert model is None


@pytest.mark.asyncio
async def test_auto_refresh(mock_fetcher, sample_model_cards):
    mock_fetcher.fetch_models_by_provider.return_value = [sample_model_cards[0]]

    registry = ModelRegistry(fetcher=mock_fetcher, refresh_interval_hours=0.0001)
    await registry.initialize()

    initial_call_count = mock_fetcher.fetch_models_by_provider.call_count

    await asyncio.sleep(0.5)

    await registry.get_model("claude-3-5-sonnet-20241022", provider="anthropic")

    assert mock_fetcher.fetch_models_by_provider.call_count > initial_call_count


@pytest.mark.asyncio
async def test_get_all_models(mock_fetcher, sample_model_cards):
    mock_fetcher.fetch_models_by_provider.side_effect = [
        [sample_model_cards[0]],
        [sample_model_cards[1]],
    ]

    registry = ModelRegistry(fetcher=mock_fetcher, refresh_interval_hours=1.0)
    await registry.initialize()

    all_models = await registry.get_all_models()

    assert len(all_models) == 2
    assert any(m.id == "claude-3-5-sonnet-20241022" for m in all_models)
    assert any(m.id == "gpt-4" for m in all_models)


@pytest.mark.asyncio
async def test_get_all_models_by_provider(mock_fetcher, sample_model_cards):
    mock_fetcher.fetch_models_by_provider.return_value = [sample_model_cards[0]]

    registry = ModelRegistry(fetcher=mock_fetcher, refresh_interval_hours=1.0)
    await registry.initialize()

    anthropic_models = await registry.get_all_models(provider="anthropic")

    assert len(anthropic_models) == 1
    assert anthropic_models[0].id == "claude-3-5-sonnet-20241022"


@pytest.mark.asyncio
async def test_refresh_all(mock_fetcher, sample_model_cards):
    mock_fetcher.fetch_models_by_provider.return_value = [sample_model_cards[0]]

    registry = ModelRegistry(fetcher=mock_fetcher, refresh_interval_hours=1.0)
    await registry.refresh_all()

    assert registry._initialized is True
    assert mock_fetcher.fetch_models_by_provider.call_count == 2


@pytest.mark.asyncio
async def test_stats(mock_fetcher, sample_model_cards):
    mock_fetcher.fetch_models_by_provider.side_effect = [
        [sample_model_cards[0]],
        [sample_model_cards[1]],
    ]

    registry = ModelRegistry(fetcher=mock_fetcher, refresh_interval_hours=1.0)
    await registry.initialize()

    stats = registry.get_stats()

    assert stats["anthropic"] == 1
    assert stats["openai"] == 1