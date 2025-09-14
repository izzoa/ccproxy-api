"""Tests for OpenAI adapter async max_tokens functionality."""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from ccproxy.adapters.openai.adapter import OpenAIAdapter


class TestOpenAIAdapterAsync:
    """Test cases for OpenAI adapter async functionality."""

    @pytest.fixture
    def adapter(self) -> OpenAIAdapter:
        """Create OpenAI adapter instance for testing."""
        return OpenAIAdapter()

    @pytest.fixture
    def basic_openai_request(self) -> dict[str, Any]:
        """Basic OpenAI request for testing."""
        return {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello, world!"}],
        }

    async def test_adapt_request_async_with_dynamic_max_tokens(
        self, adapter: OpenAIAdapter, basic_openai_request: dict[str, Any]
    ) -> None:
        """Test async adapter with dynamic max_tokens from model info service."""
        with patch(
            "ccproxy.adapters.openai.adapter.get_model_info_service"
        ) as mock_get_service:
            mock_service = AsyncMock()
            mock_service.get_max_output_tokens.return_value = 4096
            mock_get_service.return_value = mock_service

            result = await adapter.adapt_request_async(basic_openai_request)

            assert result["model"] == "claude-3-5-sonnet-20241022"  # Default mapping
            assert result["max_tokens"] == 4096  # From mock service
            mock_service.get_max_output_tokens.assert_called_once_with(
                "claude-3-5-sonnet-20241022"
            )

    async def test_adapt_request_async_with_explicit_max_tokens(
        self, adapter: OpenAIAdapter, basic_openai_request: dict[str, Any]
    ) -> None:
        """Test async adapter preserves explicit max_tokens."""
        basic_openai_request["max_tokens"] = 2048

        result = await adapter.adapt_request_async(basic_openai_request)

        assert result["max_tokens"] == 2048  # Should use explicit value
        # Should not call model info service when max_tokens is provided

    async def test_adapt_request_async_service_failure_fallback(
        self, adapter: OpenAIAdapter, basic_openai_request: dict[str, Any]
    ) -> None:
        """Test async adapter falls back to 8192 when service fails."""
        with patch(
            "ccproxy.adapters.openai.adapter.get_model_info_service"
        ) as mock_get_service:
            mock_service = AsyncMock()
            mock_service.get_max_output_tokens.side_effect = Exception(
                "Service unavailable"
            )
            mock_get_service.return_value = mock_service

            result = await adapter.adapt_request_async(basic_openai_request)

            assert result["max_tokens"] == 8192  # Fallback value
            mock_service.get_max_output_tokens.assert_called_once()

    async def test_adapt_request_async_preserves_other_parameters(
        self, adapter: OpenAIAdapter, basic_openai_request: dict[str, Any]
    ) -> None:
        """Test async adapter preserves all other request parameters."""
        basic_openai_request.update({
            "temperature": 0.7,
            "top_p": 0.9,
            "stream": True,
            "stop": ["END"],
            "system": "You are a helpful assistant",
        })

        with patch(
            "ccproxy.adapters.openai.adapter.get_model_info_service"
        ) as mock_get_service:
            mock_service = AsyncMock()
            mock_service.get_max_output_tokens.return_value = 4096
            mock_get_service.return_value = mock_service

            result = await adapter.adapt_request_async(basic_openai_request)

            assert result["temperature"] == 0.7
            assert result["top_p"] == 0.9
            assert result["stream"] is True
            assert result["stop_sequences"] == ["END"]
            assert result["max_tokens"] == 4096

    async def test_adapt_request_async_with_tools(
        self, adapter: OpenAIAdapter, basic_openai_request: dict[str, Any]
    ) -> None:
        """Test async adapter handles tool calls correctly."""
        basic_openai_request["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get current weather",
                    "parameters": {
                        "type": "object",
                        "properties": {"location": {"type": "string"}},
                    },
                },
            }
        ]
        basic_openai_request["tool_choice"] = "auto"

        with patch(
            "ccproxy.adapters.openai.adapter.get_model_info_service"
        ) as mock_get_service:
            mock_service = AsyncMock()
            mock_service.get_max_output_tokens.return_value = 4096
            mock_get_service.return_value = mock_service

            result = await adapter.adapt_request_async(basic_openai_request)

            assert "tools" in result
            assert result["tools"][0]["name"] == "get_weather"
            assert result["tool_choice"]["type"] == "auto"

    async def test_adapt_request_async_with_thinking_models(
        self, adapter: OpenAIAdapter
    ) -> None:
        """Test async adapter handles o1/o3 thinking models correctly."""
        request = {
            "model": "o1",
            "messages": [{"role": "user", "content": "Complex reasoning task"}],
            "reasoning_effort": "high",
        }

        with patch(
            "ccproxy.adapters.openai.adapter.get_model_info_service"
        ) as mock_get_service:
            mock_service = AsyncMock()
            mock_service.get_max_output_tokens.return_value = 4096
            mock_get_service.return_value = mock_service

            result = await adapter.adapt_request_async(request)

            # Should enable thinking mode
            assert "thinking" in result
            assert result["thinking"]["type"] == "enabled"
            assert result["thinking"]["budget_tokens"] == 10000  # high effort
            # Should adjust max_tokens for thinking
            assert result["max_tokens"] > 10000
            # Should set temperature to 1.0 for thinking
            assert result["temperature"] == 1.0

    async def test_adapt_request_async_different_models(
        self, adapter: OpenAIAdapter
    ) -> None:
        """Test async adapter with different model mappings."""
        test_cases = [
            ("gpt-4", "claude-3-5-sonnet-20241022"),
            ("gpt-4-turbo", "claude-3-5-sonnet-20241022"),
            ("gpt-3.5-turbo", "claude-3-5-haiku-20241022"),
        ]

        for openai_model, expected_claude_model in test_cases:
            request = {
                "model": openai_model,
                "messages": [{"role": "user", "content": "Hello"}],
            }

            with patch(
                "ccproxy.adapters.openai.adapter.get_model_info_service"
            ) as mock_get_service:
                mock_service = AsyncMock()
                mock_service.get_max_output_tokens.return_value = 4096
                mock_get_service.return_value = mock_service

                result = await adapter.adapt_request_async(request)

                assert result["model"] == expected_claude_model
                mock_service.get_max_output_tokens.assert_called_with(
                    expected_claude_model
                )

    async def test_adapt_request_sync_vs_async_compatibility(
        self, adapter: OpenAIAdapter, basic_openai_request: dict[str, Any]
    ) -> None:
        """Test that sync and async methods maintain compatibility."""
        # Add max_tokens to ensure predictable comparison
        basic_openai_request["max_tokens"] = 2048

        # Get sync result
        sync_result = adapter.adapt_request(basic_openai_request.copy())

        # Get async result
        async_result = await adapter.adapt_request_async(basic_openai_request.copy())

        # Results should be identical when max_tokens is explicitly provided
        assert sync_result["model"] == async_result["model"]
        assert sync_result["max_tokens"] == async_result["max_tokens"]
        assert sync_result["messages"] == async_result["messages"]

    async def test_adapt_request_async_error_handling(
        self, adapter: OpenAIAdapter
    ) -> None:
        """Test async adapter error handling with invalid requests."""
        invalid_request = {
            "messages": [{"role": "user", "content": "Hello"}],
            # Missing required 'model' field
        }

        with pytest.raises(ValueError, match="Invalid OpenAI request format"):
            await adapter.adapt_request_async(invalid_request)

    async def test_adapt_request_async_logging(
        self, adapter: OpenAIAdapter, basic_openai_request: dict[str, Any]
    ) -> None:
        """Test async adapter logs conversion completion."""
        with patch(
            "ccproxy.adapters.openai.adapter.get_model_info_service"
        ) as mock_get_service:
            mock_service = AsyncMock()
            mock_service.get_max_output_tokens.return_value = 4096
            mock_get_service.return_value = mock_service

            with patch("ccproxy.adapters.openai.adapter.logger") as mock_logger:
                await adapter.adapt_request_async(basic_openai_request)

                # Should log the completion
                mock_logger.debug.assert_called()
                call_args = mock_logger.debug.call_args
                assert "format_conversion_completed" in call_args[0]
                assert "adapt_request_async" in call_args[1]["operation"]