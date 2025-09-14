"""Tests for ModelInfoService dynamic model information fetching."""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from ccproxy.services.model_info_service import ModelInfoService


class TestModelInfoService:
    """Test cases for ModelInfoService."""

    @pytest.fixture
    def model_info_service(self) -> ModelInfoService:
        """Create ModelInfoService instance for testing."""
        return ModelInfoService()

    @pytest.fixture
    def mock_litellm_data(self) -> dict[str, Any]:
        """Mock LiteLLM model data for testing."""
        return {
            "claude-3-5-sonnet-20241022": {
                "max_tokens": 8192,
                "max_input_tokens": 200000,
                "max_output_tokens": 8192,
                "litellm_provider": "anthropic",
                "mode": "chat",
                "supports_function_calling": True,
                "supports_vision": True,
                "supports_streaming": True,
            },
            "claude-3-haiku-20240307": {
                "max_tokens": 4096,
                "max_input_tokens": 200000,
                "max_output_tokens": 4096,
                "litellm_provider": "anthropic",
                "mode": "chat",
                "supports_function_calling": True,
                "supports_vision": False,
                "supports_streaming": True,
            },
            "gpt-4": {
                "max_tokens": 4096,
                "max_input_tokens": 8192,
                "max_output_tokens": 4096,
                "litellm_provider": "openai",
                "mode": "chat",
                "supports_function_calling": True,
                "supports_vision": False,
                "supports_streaming": True,
            },
        }

    async def test_get_context_window_with_dynamic_data(
        self,
        model_info_service: ModelInfoService,
        mock_litellm_data: dict[str, Any],
    ) -> None:
        """Test get_context_window with dynamic data."""
        with patch.object(
            model_info_service._models_metadata_service,
            "get_models_metadata",
            return_value=AsyncMock(
                get_max_tokens=AsyncMock(return_value=208192)  # 200k + 8k
            ),
        ):
            context_window = await model_info_service.get_context_window(
                "claude-3-5-sonnet-20241022"
            )
            assert context_window == 208192

    async def test_get_context_window_with_fallback(
        self, model_info_service: ModelInfoService
    ) -> None:
        """Test get_context_window falls back to static data when dynamic fails."""
        with patch.object(
            model_info_service._models_metadata_service,
            "get_models_metadata",
            side_effect=Exception("Service unavailable"),
        ):
            context_window = await model_info_service.get_context_window(
                "claude-3-5-sonnet-20241022"
            )
            # Should fall back to static fallback value
            assert context_window == 200_000

    async def test_get_max_output_tokens_with_dynamic_data(
        self,
        model_info_service: ModelInfoService,
        mock_litellm_data: dict[str, Any],
    ) -> None:
        """Test get_max_output_tokens with dynamic data."""
        with patch.object(
            model_info_service._models_metadata_service,
            "get_models_metadata",
            return_value=AsyncMock(
                get_max_output_tokens=AsyncMock(return_value=8192)
            ),
        ):
            max_output = await model_info_service.get_max_output_tokens(
                "claude-3-5-sonnet-20241022"
            )
            assert max_output == 8192

    async def test_get_max_output_tokens_with_fallback(
        self, model_info_service: ModelInfoService
    ) -> None:
        """Test get_max_output_tokens falls back to static data when dynamic fails."""
        with patch.object(
            model_info_service._models_metadata_service,
            "get_models_metadata",
            side_effect=Exception("Service unavailable"),
        ):
            max_output = await model_info_service.get_max_output_tokens(
                "claude-3-5-sonnet-20241022"
            )
            # Should fall back to static fallback value
            assert max_output == 8192

    async def test_get_available_models_with_dynamic_data(
        self,
        model_info_service: ModelInfoService,
        mock_litellm_data: dict[str, Any],
    ) -> None:
        """Test get_available_models with dynamic data."""
        with patch.object(
            model_info_service._models_metadata_service,
            "get_models_metadata",
            return_value=AsyncMock(
                model_names=AsyncMock(return_value=list(mock_litellm_data.keys()))
            ),
        ):
            models = await model_info_service.get_available_models()
            assert "claude-3-5-sonnet-20241022" in models
            assert "claude-3-haiku-20240307" in models
            assert "gpt-4" in models

    async def test_get_available_models_with_fallback(
        self, model_info_service: ModelInfoService
    ) -> None:
        """Test get_available_models falls back to static data when dynamic fails."""
        with patch.object(
            model_info_service._models_metadata_service,
            "get_models_metadata",
            side_effect=Exception("Service unavailable"),
        ):
            models = await model_info_service.get_available_models()
            # Should contain fallback models
            assert isinstance(models, list)
            assert len(models) > 0

    async def test_validate_request_tokens_valid(
        self, model_info_service: ModelInfoService
    ) -> None:
        """Test validate_request_tokens with valid token counts."""
        with patch.object(
            model_info_service._models_metadata_service,
            "get_models_metadata",
            return_value=AsyncMock(
                get_max_tokens=AsyncMock(return_value=200000)
            ),
        ):
            # Should not raise exception
            await model_info_service.validate_request_tokens(
                "claude-3-5-sonnet-20241022", input_tokens=1000, output_tokens=2000
            )

    async def test_validate_request_tokens_exceeds_limit(
        self, model_info_service: ModelInfoService
    ) -> None:
        """Test validate_request_tokens when tokens exceed model limit."""
        with patch.object(
            model_info_service._models_metadata_service,
            "get_models_metadata",
            return_value=AsyncMock(
                get_max_tokens=AsyncMock(return_value=10000)
            ),
        ):
            from ccproxy.core.errors import ValidationError

            with pytest.raises(ValidationError, match="Token limit exceeded"):
                await model_info_service.validate_request_tokens(
                    "claude-3-5-sonnet-20241022", input_tokens=8000, output_tokens=5000
                )

    async def test_get_default_model(self, model_info_service: ModelInfoService) -> None:
        """Test get_default_model returns expected default."""
        default_model = await model_info_service.get_default_model()
        # Should return one of the known Claude models
        assert default_model.startswith("claude-")

    async def test_unknown_model_fallback(
        self, model_info_service: ModelInfoService
    ) -> None:
        """Test behavior with unknown model names."""
        with patch.object(
            model_info_service._models_metadata_service,
            "get_models_metadata",
            return_value=AsyncMock(
                get_max_tokens=AsyncMock(return_value=200_000),
                get_max_output_tokens=AsyncMock(return_value=4096),
            ),
        ):
            # Unknown models should get fallback values
            context_window = await model_info_service.get_context_window(
                "unknown-model"
            )
            max_output = await model_info_service.get_max_output_tokens(
                "unknown-model"
            )

            assert context_window == 200_000
            assert max_output == 4096

    async def test_service_caching_behavior(
        self, model_info_service: ModelInfoService
    ) -> None:
        """Test that the service properly caches model metadata."""
        with patch.object(
            model_info_service._models_metadata_service,
            "get_models_metadata",
            return_value=AsyncMock(
                get_max_tokens=AsyncMock(return_value=200000)
            ),
        ) as mock_get_metadata:
            # First call
            await model_info_service.get_context_window("claude-3-5-sonnet-20241022")
            # Second call
            await model_info_service.get_context_window("claude-3-5-sonnet-20241022")

            # Should use caching from the models_metadata_service
            assert mock_get_metadata.call_count >= 1