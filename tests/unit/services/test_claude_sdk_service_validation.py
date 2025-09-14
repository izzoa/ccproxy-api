"""Tests for ClaudeSDKService max_tokens validation functionality."""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from ccproxy.core.errors import ClaudeProxyError
from ccproxy.services.claude_sdk_service import ClaudeSDKService


class TestClaudeSDKServiceValidation:
    """Test cases for ClaudeSDKService max_tokens validation."""

    @pytest.fixture
    def claude_service(self) -> ClaudeSDKService:
        """Create ClaudeSDKService instance for testing."""
        return ClaudeSDKService()

    @pytest.fixture
    def basic_request_params(self) -> dict[str, Any]:
        """Basic request parameters for testing."""
        return {
            "model": "claude-3-5-sonnet-20241022",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 4096,
        }

    async def test_create_completion_with_valid_max_tokens(
        self,
        claude_service: ClaudeSDKService,
        basic_request_params: dict[str, Any],
    ) -> None:
        """Test create_completion with valid max_tokens."""
        with patch(
            "ccproxy.services.claude_sdk_service.get_model_info_service"
        ) as mock_get_service:
            mock_service = AsyncMock()
            mock_service.get_max_output_tokens.return_value = 8192  # Model supports 8k
            mock_get_service.return_value = mock_service

            with patch.object(
                claude_service, "_create_completion_internal"
            ) as mock_internal:
                mock_internal.return_value = {"id": "test", "content": []}

                # Should not raise exception
                await claude_service.create_completion(**basic_request_params)

                mock_service.get_max_output_tokens.assert_called_once_with(
                    "claude-3-5-sonnet-20241022"
                )
                mock_internal.assert_called_once()

    async def test_create_completion_with_max_tokens_exceeds_limit(
        self,
        claude_service: ClaudeSDKService,
        basic_request_params: dict[str, Any],
    ) -> None:
        """Test create_completion when max_tokens exceeds model limit."""
        basic_request_params["max_tokens"] = 10000  # Request 10k tokens

        with patch(
            "ccproxy.services.claude_sdk_service.get_model_info_service"
        ) as mock_get_service:
            mock_service = AsyncMock()
            mock_service.get_max_output_tokens.return_value = 8192  # Model supports 8k
            mock_get_service.return_value = mock_service

            with pytest.raises(ClaudeProxyError) as exc_info:
                await claude_service.create_completion(**basic_request_params)

            error = exc_info.value
            assert error.status_code == 400
            assert error.error_type == "invalid_request_error"
            assert "10000" in error.message  # Requested amount
            assert "8192" in error.message   # Model limit
            assert "claude-3-5-sonnet-20241022" in error.message

    async def test_create_completion_without_max_tokens(
        self,
        claude_service: ClaudeSDKService,
        basic_request_params: dict[str, Any],
    ) -> None:
        """Test create_completion without max_tokens parameter."""
        del basic_request_params["max_tokens"]

        with patch.object(
            claude_service, "_create_completion_internal"
        ) as mock_internal:
            mock_internal.return_value = {"id": "test", "content": []}

            # Should not validate or call model info service
            await claude_service.create_completion(**basic_request_params)

            mock_internal.assert_called_once()

    async def test_create_completion_with_none_max_tokens(
        self,
        claude_service: ClaudeSDKService,
        basic_request_params: dict[str, Any],
    ) -> None:
        """Test create_completion with max_tokens set to None."""
        basic_request_params["max_tokens"] = None

        with patch.object(
            claude_service, "_create_completion_internal"
        ) as mock_internal:
            mock_internal.return_value = {"id": "test", "content": []}

            # Should not validate when max_tokens is None
            await claude_service.create_completion(**basic_request_params)

            mock_internal.assert_called_once()

    async def test_create_completion_with_model_info_service_failure(
        self,
        claude_service: ClaudeSDKService,
        basic_request_params: dict[str, Any],
    ) -> None:
        """Test create_completion when model info service fails."""
        with patch(
            "ccproxy.services.claude_sdk_service.get_model_info_service"
        ) as mock_get_service:
            mock_service = AsyncMock()
            mock_service.get_max_output_tokens.side_effect = Exception(
                "Service unavailable"
            )
            mock_get_service.return_value = mock_service

            with patch.object(
                claude_service, "_create_completion_internal"
            ) as mock_internal:
                mock_internal.return_value = {"id": "test", "content": []}

                # Should continue processing if service fails
                await claude_service.create_completion(**basic_request_params)

                mock_internal.assert_called_once()

    async def test_create_completion_with_different_models(
        self, claude_service: ClaudeSDKService
    ) -> None:
        """Test create_completion validation with different models."""
        test_cases = [
            ("claude-3-5-sonnet-20241022", 8192, 4096, False),  # Valid
            ("claude-3-5-sonnet-20241022", 4096, 10000, True),  # Exceeds
            ("claude-3-haiku-20240307", 4096, 2048, False),     # Valid
            ("claude-3-haiku-20240307", 2048, 8192, True),      # Exceeds
        ]

        for model, model_limit, requested_tokens, should_fail in test_cases:
            request_params = {
                "model": model,
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": requested_tokens,
            }

            with patch(
                "ccproxy.services.claude_sdk_service.get_model_info_service"
            ) as mock_get_service:
                mock_service = AsyncMock()
                mock_service.get_max_output_tokens.return_value = model_limit
                mock_get_service.return_value = mock_service

                if should_fail:
                    with pytest.raises(ClaudeProxyError):
                        await claude_service.create_completion(**request_params)
                else:
                    with patch.object(
                        claude_service, "_create_completion_internal"
                    ) as mock_internal:
                        mock_internal.return_value = {"id": "test", "content": []}
                        # Should not raise exception
                        await claude_service.create_completion(**request_params)

    async def test_create_completion_streaming_with_max_tokens_validation(
        self,
        claude_service: ClaudeSDKService,
        basic_request_params: dict[str, Any],
    ) -> None:
        """Test create_completion_streaming also validates max_tokens."""
        basic_request_params["max_tokens"] = 10000  # Request 10k tokens
        basic_request_params["stream"] = True

        with patch(
            "ccproxy.services.claude_sdk_service.get_model_info_service"
        ) as mock_get_service:
            mock_service = AsyncMock()
            mock_service.get_max_output_tokens.return_value = 8192  # Model supports 8k
            mock_get_service.return_value = mock_service

            with pytest.raises(ClaudeProxyError) as exc_info:
                async for _ in claude_service.create_completion_streaming(
                    **basic_request_params
                ):
                    pass

            error = exc_info.value
            assert error.status_code == 400
            assert error.error_type == "invalid_request_error"

    async def test_validation_error_message_format(
        self,
        claude_service: ClaudeSDKService,
        basic_request_params: dict[str, Any],
    ) -> None:
        """Test validation error message contains all required information."""
        basic_request_params["max_tokens"] = 15000
        basic_request_params["model"] = "claude-3-5-haiku-20241022"

        with patch(
            "ccproxy.services.claude_sdk_service.get_model_info_service"
        ) as mock_get_service:
            mock_service = AsyncMock()
            mock_service.get_max_output_tokens.return_value = 4096
            mock_get_service.return_value = mock_service

            with pytest.raises(ClaudeProxyError) as exc_info:
                await claude_service.create_completion(**basic_request_params)

            error = exc_info.value
            error_message = error.message

            # Should contain all key information
            assert "15000" in error_message  # Requested tokens
            assert "4096" in error_message   # Model limit
            assert "claude-3-5-haiku-20241022" in error_message  # Model name
            assert "maximum" in error_message.lower()

    async def test_validation_preserves_other_parameters(
        self,
        claude_service: ClaudeSDKService,
        basic_request_params: dict[str, Any],
    ) -> None:
        """Test that validation doesn't interfere with other parameters."""
        basic_request_params.update({
            "temperature": 0.7,
            "top_p": 0.9,
            "system": "You are a helpful assistant",
            "tools": [{"name": "test_tool", "description": "Test"}],
        })

        with patch(
            "ccproxy.services.claude_sdk_service.get_model_info_service"
        ) as mock_get_service:
            mock_service = AsyncMock()
            mock_service.get_max_output_tokens.return_value = 8192
            mock_get_service.return_value = mock_service

            with patch.object(
                claude_service, "_create_completion_internal"
            ) as mock_internal:
                mock_internal.return_value = {"id": "test", "content": []}

                await claude_service.create_completion(**basic_request_params)

                # Verify all parameters were passed through
                call_args = mock_internal.call_args[1]
                assert call_args["temperature"] == 0.7
                assert call_args["top_p"] == 0.9
                assert call_args["system"] == "You are a helpful assistant"
                assert "tools" in call_args

    async def test_edge_case_zero_max_tokens(
        self,
        claude_service: ClaudeSDKService,
        basic_request_params: dict[str, Any],
    ) -> None:
        """Test validation with edge case of zero max_tokens."""
        basic_request_params["max_tokens"] = 0

        with patch(
            "ccproxy.services.claude_sdk_service.get_model_info_service"
        ) as mock_get_service:
            mock_service = AsyncMock()
            mock_service.get_max_output_tokens.return_value = 4096
            mock_get_service.return_value = mock_service

            with pytest.raises(ClaudeProxyError) as exc_info:
                await claude_service.create_completion(**basic_request_params)

            error = exc_info.value
            assert error.status_code == 400
            assert "0" in error.message

    async def test_edge_case_negative_max_tokens(
        self,
        claude_service: ClaudeSDKService,
        basic_request_params: dict[str, Any],
    ) -> None:
        """Test validation with edge case of negative max_tokens."""
        basic_request_params["max_tokens"] = -100

        with patch(
            "ccproxy.services.claude_sdk_service.get_model_info_service"
        ) as mock_get_service:
            mock_service = AsyncMock()
            mock_service.get_max_output_tokens.return_value = 4096
            mock_get_service.return_value = mock_service

            with pytest.raises(ClaudeProxyError) as exc_info:
                await claude_service.create_completion(**basic_request_params)

            error = exc_info.value
            assert error.status_code == 400
            assert "-100" in error.message or "negative" in error.message.lower()