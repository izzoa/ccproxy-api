import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import httpx
import pytest

from ccproxy.utils.model_fetcher import ModelFetcher


@pytest.fixture
def sample_litellm_data():
    return {
        "claude-3-5-sonnet-20241022": {
            "litellm_provider": "anthropic",
            "max_input_tokens": 200000,
            "max_output_tokens": 8192,
            "max_tokens": 200000,
            "supports_vision": True,
            "supports_function_calling": True,
            "supports_parallel_function_calling": True,
            "mode": "chat",
        },
        "gpt-4": {
            "litellm_provider": "openai",
            "max_input_tokens": 8192,
            "max_output_tokens": 4096,
            "max_tokens": 8192,
            "supports_vision": False,
            "supports_function_calling": True,
            "mode": "chat",
        },
        "text-embedding-3-small": {
            "litellm_provider": "openai",
            "mode": "embedding",
        },
    }


@pytest.fixture
def temp_cache_dir(tmp_path):
    return tmp_path / "cache"


@pytest.mark.asyncio
async def test_fetch_from_url(sample_litellm_data):
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = sample_litellm_data
    mock_response.raise_for_status = MagicMock()

    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_response)

    fetcher = ModelFetcher()
    fetcher._client = mock_client

    result = await fetcher._fetch_from_url()

    assert result == sample_litellm_data
    mock_client.get.assert_called_once_with(fetcher.source_url)


@pytest.mark.asyncio
async def test_fetch_with_cache(sample_litellm_data, temp_cache_dir):
    temp_cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = temp_cache_dir / "litellm_models.json"
    cache_file.write_text(json.dumps(sample_litellm_data))

    fetcher = ModelFetcher(cache_dir=temp_cache_dir)

    result = await fetcher.fetch_all_models(use_cache=True)

    assert result == sample_litellm_data


@pytest.mark.asyncio
async def test_cache_invalidation(sample_litellm_data, temp_cache_dir):
    temp_cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = temp_cache_dir / "litellm_models.json"
    cache_file.write_text(json.dumps(sample_litellm_data))

    old_time = (datetime.now(UTC) - timedelta(hours=25)).timestamp()
    cache_file.touch()
    import os

    os.utime(cache_file, (old_time, old_time))

    fetcher = ModelFetcher(cache_dir=temp_cache_dir, cache_ttl_hours=24)

    is_valid = fetcher._is_cache_valid(cache_file)

    assert is_valid is False


@pytest.mark.asyncio
async def test_memory_cache(sample_litellm_data):
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = sample_litellm_data
    mock_response.raise_for_status = MagicMock()

    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_response)

    fetcher = ModelFetcher()
    fetcher._client = mock_client

    result1 = await fetcher.fetch_all_models(use_cache=True)
    result2 = await fetcher.fetch_all_models(use_cache=True)

    assert result1 == result2
    assert mock_client.get.call_count == 1


@pytest.mark.asyncio
async def test_fetch_models_by_provider(sample_litellm_data):
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = sample_litellm_data
    mock_response.raise_for_status = MagicMock()

    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_response)

    fetcher = ModelFetcher()
    fetcher._client = mock_client

    anthropic_models = await fetcher.fetch_models_by_provider(provider="anthropic")

    assert len(anthropic_models) == 1
    assert anthropic_models[0].id == "claude-3-5-sonnet-20241022"
    assert anthropic_models[0].owned_by == "anthropic"


@pytest.mark.asyncio
async def test_fetch_models_filters_embeddings(sample_litellm_data):
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = sample_litellm_data
    mock_response.raise_for_status = MagicMock()

    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_response)

    fetcher = ModelFetcher()
    fetcher._client = mock_client

    all_models = await fetcher.fetch_models_by_provider(provider="all")

    assert len(all_models) == 2
    assert not any(m.id == "text-embedding-3-small" for m in all_models)


@pytest.mark.asyncio
async def test_http_client_reuse():
    fetcher = ModelFetcher()

    client1 = fetcher._get_client()
    client2 = fetcher._get_client()

    assert client1 is client2

    await fetcher.aclose()
    assert fetcher._client is None


@pytest.mark.asyncio
async def test_model_card_conversion(sample_litellm_data):
    fetcher = ModelFetcher()

    model_data = sample_litellm_data["claude-3-5-sonnet-20241022"]
    card = fetcher._convert_to_model_card("claude-3-5-sonnet-20241022", model_data)

    assert card is not None
    assert card.id == "claude-3-5-sonnet-20241022"
    assert card.owned_by == "anthropic"
    assert card.max_input_tokens == 200000
    assert card.supports_vision is True
    assert card.supports_function_calling is True


@pytest.mark.asyncio
async def test_fetch_http_error():
    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(side_effect=httpx.HTTPError("Connection failed"))

    fetcher = ModelFetcher()
    fetcher._client = mock_client

    result = await fetcher._fetch_from_url()

    assert result is None