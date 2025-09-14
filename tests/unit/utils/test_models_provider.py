"""Tests for models_provider dynamic model list functionality."""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from ccproxy.utils.models_provider import (
    get_anthropic_models,
    get_anthropic_models_async,
    get_models_list,
    get_models_list_async,
    get_openai_models,
    get_openai_models_async,
)


class TestModelsProvider:
    """Test cases for dynamic model list provider."""

    @pytest.fixture
    def mock_available_models(self) -> list[str]:
        """Mock list of available models from ModelInfoService."""
        return [
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
            "claude-3-haiku-20240307",
            "gpt-4o",
            "gpt-4o-mini",
            "o1",
            "o3-mini",
        ]

    async def test_get_anthropic_models_async_with_dynamic_data(
        self, mock_available_models: list[str]
    ) -> None:
        """Test get_anthropic_models_async with dynamic model data."""
        with patch(
            "ccproxy.utils.models_provider.get_model_info_service"
        ) as mock_get_service:
            mock_service = AsyncMock()
            mock_service.get_available_models.return_value = mock_available_models
            mock_get_service.return_value = mock_service

            result = await get_anthropic_models_async()

            # Should only include Claude models
            claude_models = [model for model in result if model["id"].startswith("claude-")]
            assert len(claude_models) == 3
            assert any(m["id"] == "claude-3-5-sonnet-20241022" for m in result)
            assert any(m["id"] == "claude-3-5-haiku-20241022" for m in result)
            assert any(m["id"] == "claude-3-haiku-20240307" for m in result)

            # Should not include non-Claude models
            assert not any(m["id"] == "gpt-4o" for m in result)

            # Check structure
            for model in result:
                assert "type" in model
                assert model["type"] == "model"
                assert "id" in model
                assert "display_name" in model
                assert "created_at" in model
                assert isinstance(model["created_at"], int)

    async def test_get_anthropic_models_async_with_service_failure(self) -> None:
        """Test get_anthropic_models_async falls back when service fails."""
        with patch(
            "ccproxy.utils.models_provider.get_model_info_service"
        ) as mock_get_service:
            mock_get_service.side_effect = Exception("Service unavailable")

            result = await get_anthropic_models_async()

            # Should return fallback models
            assert isinstance(result, list)
            assert len(result) > 0
            assert all(model["id"].startswith("claude-") for model in result)

    def test_get_anthropic_models_sync_wrapper(self, mock_available_models: list[str]) -> None:
        """Test get_anthropic_models sync wrapper calls async version."""
        with patch(
            "ccproxy.utils.models_provider.get_model_info_service"
        ) as mock_get_service:
            mock_service = AsyncMock()
            mock_service.get_available_models.return_value = mock_available_models
            mock_get_service.return_value = mock_service

            result = get_anthropic_models()

            # Should call async version and return results
            assert isinstance(result, list)
            assert len(result) > 0
            claude_models = [m for m in result if m["id"].startswith("claude-")]
            assert len(claude_models) > 0

    async def test_get_openai_models_async_with_dynamic_data(
        self, mock_available_models: list[str]
    ) -> None:
        """Test get_openai_models_async with dynamic model data."""
        with patch(
            "ccproxy.utils.models_provider.get_model_info_service"
        ) as mock_get_service:
            mock_service = AsyncMock()
            mock_service.get_available_models.return_value = mock_available_models
            mock_get_service.return_value = mock_service

            result = await get_openai_models_async()

            # Should only include OpenAI models
            openai_models = [m for m in result if not m["id"].startswith("claude-")]
            assert len(openai_models) == 4  # gpt-4o, gpt-4o-mini, o1, o3-mini
            assert any(m["id"] == "gpt-4o" for m in result)
            assert any(m["id"] == "gpt-4o-mini" for m in result)
            assert any(m["id"] == "o1" for m in result)
            assert any(m["id"] == "o3-mini" for m in result)

            # Should not include Claude models
            assert not any(m["id"].startswith("claude-") for m in result)

            # Check structure
            for model in result:
                assert "id" in model
                assert "object" in model
                assert model["object"] == "model"
                assert "created" in model
                assert "owned_by" in model
                assert model["owned_by"] == "openai"
                assert isinstance(model["created"], int)

    async def test_get_openai_models_async_with_service_failure(self) -> None:
        """Test get_openai_models_async falls back when service fails."""
        with patch(
            "ccproxy.utils.models_provider.get_model_info_service"
        ) as mock_get_service:
            mock_get_service.side_effect = Exception("Service unavailable")

            result = await get_openai_models_async()

            # Should return fallback models
            assert isinstance(result, list)
            assert len(result) > 0
            assert all(not model["id"].startswith("claude-") for model in result)

    def test_get_openai_models_sync_wrapper(self, mock_available_models: list[str]) -> None:
        """Test get_openai_models sync wrapper calls async version."""
        with patch(
            "ccproxy.utils.models_provider.get_model_info_service"
        ) as mock_get_service:
            mock_service = AsyncMock()
            mock_service.get_available_models.return_value = mock_available_models
            mock_get_service.return_value = mock_service

            result = get_openai_models()

            # Should call async version and return results
            assert isinstance(result, list)
            assert len(result) > 0
            openai_models = [m for m in result if not m["id"].startswith("claude-")]
            assert len(openai_models) > 0

    async def test_get_models_list_async_combines_models(
        self, mock_available_models: list[str]
    ) -> None:
        """Test get_models_list_async combines Claude and OpenAI models."""
        with patch(
            "ccproxy.utils.models_provider.get_model_info_service"
        ) as mock_get_service:
            mock_service = AsyncMock()
            mock_service.get_available_models.return_value = mock_available_models
            mock_get_service.return_value = mock_service

            result = await get_models_list_async()

            # Check structure
            assert "data" in result
            assert "has_more" in result
            assert "object" in result
            assert result["has_more"] is False
            assert result["object"] == "list"

            # Should contain both Claude and OpenAI models
            models = result["data"]
            claude_models = [m for m in models if m.get("id", "").startswith("claude-")]
            openai_models = [m for m in models if not m.get("id", "").startswith("claude-")]

            assert len(claude_models) > 0
            assert len(openai_models) > 0
            assert len(models) == len(claude_models) + len(openai_models)

    async def test_get_models_list_async_with_service_failure(self) -> None:
        """Test get_models_list_async falls back when service fails."""
        with patch(
            "ccproxy.utils.models_provider.get_model_info_service"
        ) as mock_get_service:
            mock_get_service.side_effect = Exception("Service unavailable")

            result = await get_models_list_async()

            # Should return fallback response
            assert "data" in result
            assert isinstance(result["data"], list)
            assert len(result["data"]) > 0

    def test_get_models_list_sync_wrapper(self, mock_available_models: list[str]) -> None:
        """Test get_models_list sync wrapper calls async version."""
        with patch(
            "ccproxy.utils.models_provider.get_model_info_service"
        ) as mock_get_service:
            mock_service = AsyncMock()
            mock_service.get_available_models.return_value = mock_available_models
            mock_get_service.return_value = mock_service

            result = get_models_list()

            # Should call async version and return combined results
            assert "data" in result
            assert isinstance(result["data"], list)
            assert len(result["data"]) > 0

    async def test_display_names_are_applied(self, mock_available_models: list[str]) -> None:
        """Test that display names are properly applied to models."""
        with patch(
            "ccproxy.utils.models_provider.get_model_info_service"
        ) as mock_get_service:
            mock_service = AsyncMock()
            mock_service.get_available_models.return_value = mock_available_models
            mock_get_service.return_value = mock_service

            result = await get_anthropic_models_async()

            # Check specific display names
            sonnet_model = next(
                (m for m in result if m["id"] == "claude-3-5-sonnet-20241022"), None
            )
            haiku_model = next(
                (m for m in result if m["id"] == "claude-3-5-haiku-20241022"), None
            )

            assert sonnet_model is not None
            assert haiku_model is not None
            assert sonnet_model["display_name"] == "Claude Sonnet 3.5 (New)"
            assert haiku_model["display_name"] == "Claude Haiku 3.5"

    async def test_model_timestamps_are_applied(self, mock_available_models: list[str]) -> None:
        """Test that model timestamps are properly applied."""
        with patch(
            "ccproxy.utils.models_provider.get_model_info_service"
        ) as mock_get_service:
            mock_service = AsyncMock()
            mock_service.get_available_models.return_value = mock_available_models
            mock_get_service.return_value = mock_service

            claude_result = await get_anthropic_models_async()
            openai_result = await get_openai_models_async()

            # Check that timestamps are integers and reasonable
            for model in claude_result:
                assert isinstance(model["created_at"], int)
                assert model["created_at"] > 1600000000  # After 2020

            for model in openai_result:
                assert isinstance(model["created"], int)
                assert model["created"] > 1600000000  # After 2020

    def test_openai_model_filtering_patterns(self) -> None:
        """Test that OpenAI model filtering patterns work correctly."""
        test_models = [
            "gpt-4o",
            "gpt-4o-mini", 
            "gpt-4-turbo",
            "o1",
            "o1-mini",
            "o3",
            "o3-mini",
            "claude-3-5-sonnet-20241022",
            "random-model",
        ]

        with patch(
            "ccproxy.utils.models_provider.get_model_info_service"
        ) as mock_get_service:
            mock_service = AsyncMock()
            mock_service.get_available_models.return_value = test_models
            mock_get_service.return_value = mock_service

            result = get_openai_models()

            # Should include all OpenAI-pattern models
            model_ids = [m["id"] for m in result]
            assert "gpt-4o" in model_ids
            assert "gpt-4o-mini" in model_ids
            assert "gpt-4-turbo" in model_ids
            assert "o1" in model_ids
            assert "o1-mini" in model_ids
            assert "o3" in model_ids
            assert "o3-mini" in model_ids

            # Should exclude non-OpenAI models
            assert "claude-3-5-sonnet-20241022" not in model_ids
            assert "random-model" not in model_ids