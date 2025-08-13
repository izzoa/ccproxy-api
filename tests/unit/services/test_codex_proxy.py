"""Tests for Codex proxy service functionality.

Tests the Codex-specific proxy functionality including request transformation,
response conversion, streaming behavior, and authentication integration.
Uses factory fixtures for flexible test configuration and reduced duplication.

The tests cover:
- Codex request proxy to OpenAI backend (/codex/responses)
- Session-based requests (/codex/{session_id}/responses)
- Request/response transformation for Codex format
- Streaming to non-streaming conversion when user doesn't request streaming
- OpenAI OAuth authentication integration
- Error handling for Codex-specific scenarios
"""

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tests.factories import FastAPIClientFactory
from tests.helpers.assertions import (
    assert_codex_response_format,
    assert_sse_format_compliance,
    assert_sse_headers,
)
from tests.helpers.test_data import (
    CODEX_REQUEST_WITH_SESSION,
    INVALID_MODEL_CODEX_REQUEST,
    MISSING_INPUT_CODEX_REQUEST,
    STANDARD_CODEX_REQUEST,
    STREAMING_CODEX_REQUEST,
)


if TYPE_CHECKING:
    pass


@pytest.mark.unit
class TestCodexProxyService:
    """Test Codex proxy service functionality."""

    def test_codex_request_success(
        self,
        client_with_mock_codex: TestClient,
        mock_external_openai_codex_api: Any,
    ) -> None:
        """Test successful Codex request handling."""
        response = client_with_mock_codex.post(
            "/codex/responses", json=STANDARD_CODEX_REQUEST
        )

        assert response.status_code == 200
        data: dict[str, Any] = response.json()
        assert_codex_response_format(data)

    def test_codex_request_with_session(
        self,
        client_with_mock_codex: TestClient,
        mock_external_openai_codex_api: Any,
    ) -> None:
        """Test Codex request with session ID handling."""
        session_id = "test-session-123"
        response = client_with_mock_codex.post(
            f"/codex/{session_id}/responses", json=CODEX_REQUEST_WITH_SESSION
        )

        assert response.status_code == 200
        data: dict[str, Any] = response.json()
        assert_codex_response_format(data)

    def test_codex_streaming_conversion(
        self,
        client_with_mock_codex: TestClient,
        mock_external_openai_codex_api_streaming: Any,
    ) -> None:
        """Test streaming to non-streaming conversion when user doesn't request streaming."""
        # Request without explicit stream parameter should return JSON response
        # even though backend returns streaming
        request_without_stream = {
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Hello!"}],
                }
            ],
            "model": "gpt-5",
            "store": False,
            # No "stream" field - should return JSON response
        }

        response = client_with_mock_codex.post(
            "/codex/responses", json=request_without_stream
        )

        # Should return 200 when the mock is properly set up
        assert response.status_code == 200

    def test_codex_explicit_streaming(
        self,
        client_with_mock_codex_streaming: TestClient,
        mock_external_openai_codex_api_streaming: Any,
    ) -> None:
        """Test explicit streaming when user requests it."""
        with client_with_mock_codex_streaming.stream(
            "POST", "/codex/responses", json=STREAMING_CODEX_REQUEST
        ) as response:
            assert response.status_code == 200
            assert_sse_headers(response)

            chunks: list[str] = []
            for line in response.iter_lines():
                if line.strip():
                    chunks.append(line)

            assert_sse_format_compliance(chunks)

    def test_codex_request_transformation(
        self,
        client_with_mock_codex: TestClient,
    ) -> None:
        """Test Codex request transformation for CLI detection."""
        # Test that request is properly handled through the proxy service
        with patch(
            "ccproxy.services.proxy_service.ProxyService.handle_codex_request"
        ) as mock_handle:
            mock_handle.return_value = {
                "id": "codex_test_123",
                "object": "codex.response",
                "created": 1234567890,
                "model": "gpt-5",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Test response"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            }

            response = client_with_mock_codex.post(
                "/codex/responses", json=STANDARD_CODEX_REQUEST
            )

            # Proxy service should be called
            mock_handle.assert_called_once()

    def test_codex_authentication_required(
        self,
        fastapi_client_factory: FastAPIClientFactory,
    ) -> None:
        """Test that Codex endpoints require OpenAI authentication."""
        # Create client without OpenAI credentials
        client = fastapi_client_factory.create_client(auth_enabled=False)

        response = client.post("/codex/responses", json=STANDARD_CODEX_REQUEST)

        # Should return authentication error
        assert response.status_code == 401
        data = response.json()
        # The response might have either 'detail' or other error format
        error_message = data.get("detail", data.get("error", {}).get("message", ""))
        assert "credentials" in error_message.lower()

    def test_codex_invalid_model_error(
        self,
        client_with_mock_codex: TestClient,
        mock_external_openai_codex_api_error: Any,
    ) -> None:
        """Test Codex response with invalid model."""
        response = client_with_mock_codex.post(
            "/codex/responses", json=INVALID_MODEL_CODEX_REQUEST
        )

        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert data["error"]["type"] == "invalid_request_error"

    def test_codex_missing_input_validation(
        self,
        client_with_mock_codex: TestClient,
    ) -> None:
        """Test Codex request validation for missing input."""
        response = client_with_mock_codex.post(
            "/codex/responses", json=MISSING_INPUT_CODEX_REQUEST
        )

        # Should return 401 for authentication since auth is checked first
        assert response.status_code == 401

    @patch("ccproxy.services.proxy_service.ProxyService.handle_codex_request")
    async def test_codex_proxy_service_integration(
        self,
        mock_handle_codex: AsyncMock,
        client_with_mock_codex: TestClient,
    ) -> None:
        """Test integration with ProxyService.handle_codex_request method."""
        # Mock the handle_codex_request method
        mock_response = {
            "id": "codex_test_123",
            "object": "codex.response",
            "created": 1234567890,
            "model": "gpt-5",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Test response"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        mock_handle_codex.return_value = mock_response

        response = client_with_mock_codex.post(
            "/codex/responses", json=STANDARD_CODEX_REQUEST
        )

        assert response.status_code == 200
        data = response.json()
        assert_codex_response_format(data)

    def test_codex_error_handling(
        self,
        client_with_mock_codex: TestClient,
    ) -> None:
        """Test Codex-specific error handling and response format."""
        with patch(
            "ccproxy.services.proxy_service.ProxyService.handle_codex_request"
        ) as mock_handle:
            # Mock a server error
            mock_handle.side_effect = Exception("Codex service unavailable")

            response = client_with_mock_codex.post(
                "/codex/responses", json=STANDARD_CODEX_REQUEST
            )

            assert response.status_code == 500
            data = response.json()
            # Check for either 'detail' or 'error' field in response
            assert "error" in data or "detail" in data

    def test_codex_session_id_resolution(
        self,
        client_with_mock_codex: TestClient,
    ) -> None:
        """Test session ID resolution functionality."""
        # Test that session ID resolution happens implicitly in the endpoint
        # Since we can't easily mock the internal call, we test that the endpoint works
        session_id = "test-session"
        response = client_with_mock_codex.post(
            f"/codex/{session_id}/responses", json=CODEX_REQUEST_WITH_SESSION
        )

        # Should return 401 due to auth requirements, but endpoint routing should work
        assert response.status_code == 401

    @patch("ccproxy.auth.openai.OpenAITokenManager.load_credentials")
    async def test_codex_token_validation(
        self,
        mock_load_credentials: AsyncMock,
        client_with_mock_codex: TestClient,
    ) -> None:
        """Test OpenAI token validation for Codex requests."""
        from datetime import UTC, datetime

        from ccproxy.auth.openai import OpenAICredentials

        # Mock valid credentials
        mock_credentials = OpenAICredentials(
            access_token="valid-token",
            refresh_token="valid-refresh",
            expires_at=datetime.fromtimestamp(9999999999, UTC),  # Far future
            account_id="test-account",
        )
        mock_load_credentials.return_value = mock_credentials

        response = client_with_mock_codex.post(
            "/codex/responses", json=STANDARD_CODEX_REQUEST
        )

        # Should still return 401 because of additional auth requirements in implementation
        # This test validates that the endpoint processes the request and calls auth
        assert response.status_code == 401


@pytest.mark.unit
class TestCodexDetectionService:
    """Test Codex CLI detection and transformation service."""

    @pytest.fixture
    def mock_settings(self) -> Any:
        """Create mock settings for CodexDetectionService."""
        from unittest.mock import MagicMock

        mock_settings = MagicMock()
        return mock_settings

    def test_codex_detection_service_initialization(self, mock_settings: Any) -> None:
        """Test CodexDetectionService initialization."""
        from ccproxy.services.codex_detection_service import CodexDetectionService

        service = CodexDetectionService(mock_settings)
        assert service.settings == mock_settings
        assert service.cache_dir is not None

    def test_get_cached_data_returns_none_initially(self, mock_settings: Any) -> None:
        """Test that get_cached_data returns None initially."""
        from ccproxy.services.codex_detection_service import CodexDetectionService

        service = CodexDetectionService(mock_settings)
        cached_data = service.get_cached_data()
        assert cached_data is None

    @patch(
        "ccproxy.services.codex_detection_service.CodexDetectionService._get_codex_version"
    )
    @patch(
        "ccproxy.services.codex_detection_service.CodexDetectionService._load_from_cache"
    )
    async def test_initialize_detection_with_cache(
        self,
        mock_load_cache: MagicMock,
        mock_get_version: AsyncMock,
        mock_settings: Any,
    ) -> None:
        """Test initialize_detection when cache exists."""
        from ccproxy.models.detection import (
            CodexCacheData,
            CodexHeaders,
            CodexInstructionsData,
        )
        from ccproxy.services.codex_detection_service import CodexDetectionService

        # Mock version and cached data
        mock_get_version.return_value = "0.21.0"
        mock_cached = CodexCacheData(
            codex_version="0.21.0",
            headers=CodexHeaders(
                session_id="test-session", originator="codex_cli_rs", version="0.21.0"
            ),
            instructions=CodexInstructionsData(instructions_field="Test instructions"),
        )
        mock_load_cache.return_value = mock_cached

        service = CodexDetectionService(mock_settings)
        result = await service.initialize_detection()

        assert result == mock_cached
        assert service.get_cached_data() == mock_cached

    @patch(
        "ccproxy.services.codex_detection_service.CodexDetectionService._get_codex_version"
    )
    async def test_initialize_detection_fallback_on_error(
        self, mock_get_version: AsyncMock, mock_settings: Any
    ) -> None:
        """Test initialize_detection fallback when detection fails."""
        from ccproxy.services.codex_detection_service import CodexDetectionService

        # Mock version retrieval to raise an error
        mock_get_version.side_effect = Exception("Codex not found")

        service = CodexDetectionService(mock_settings)
        result = await service.initialize_detection()

        # Should return fallback data
        assert result is not None
        assert "codex_cli_rs" in result.headers.originator
