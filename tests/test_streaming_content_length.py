"""Tests for streaming response Content-Length header handling."""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request
from fastapi.responses import StreamingResponse

from ccproxy.adapters.base import APIAdapter
from ccproxy.auth.manager import AuthManager
from ccproxy.services.handler_config import HandlerConfig
from ccproxy.services.proxy_service import ProxyService


class MockAuthManager(AuthManager):
    """Mock authentication manager for testing."""

    async def get_access_token(self) -> str:
        """Get mock access token."""
        return "test-token"

    async def get_credentials(self) -> Any:
        """Get mock credentials."""
        from ccproxy.auth.models import ClaudeCredentials, OAuthToken

        oauth_token = OAuthToken(
            accessToken="test-token",
            refreshToken="test-refresh",
            expiresAt=None,
            scopes=["test"],
            subscriptionType="test",
            tokenType="Bearer",
        )
        return ClaudeCredentials(claudeAiOauth=oauth_token)

    async def is_authenticated(self) -> bool:
        """Mock authentication check."""
        return True

    async def get_user_profile(self) -> Any:
        """Get mock user profile."""
        return None

    async def get_auth_headers(self) -> dict[str, str]:
        """Get mock auth headers."""
        return {"authorization": "Bearer test-token"}

    async def validate_credentials(self) -> bool:
        """Mock validation always returns True."""
        return True

    def get_provider_name(self) -> str:
        """Get mock provider name."""
        return "test-provider"

    async def __aenter__(self) -> "MockAuthManager":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        pass


@pytest.fixture
def mock_proxy_service():
    """Create a mock ProxyService instance."""
    proxy_client = AsyncMock()
    credentials_manager = AsyncMock()
    settings = MagicMock()
    settings.claude.base_url = "https://api.anthropic.com"

    service = ProxyService(
        proxy_client=proxy_client,
        credentials_manager=credentials_manager,
        settings=settings,
    )
    service.metrics = None  # type: ignore[assignment]

    return service


@pytest.fixture
def mock_streaming_request():
    """Create a mock streaming FastAPI Request."""
    request = AsyncMock(spec=Request)
    request.method = "POST"
    request.url.path = "/api/codex/chat/completions"
    request.url.query = None
    request.headers = {"content-type": "application/json"}
    request.state.request_id = "test-request-123"
    request.state.context = MagicMock()
    request.body.return_value = (
        b'{"messages": [{"role": "user", "content": "Hello"}], '
        b'"model": "gpt-4", "stream": true}'
    )

    return request


@pytest.mark.asyncio
async def test_streaming_response_no_content_length(
    mock_proxy_service, mock_streaming_request
):
    """Test that Content-Length header is not passed through for streaming responses."""
    auth_manager = MockAuthManager()

    context = HandlerConfig(
        provider_name="codex",
        auth_manager=auth_manager,
        target_base_url="https://chatgpt.com/backend-api/codex",
        supports_streaming=True,
    )

    # Mock the streaming response with Content-Length header from upstream
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        mock_response = AsyncMock()
        mock_response.status_code = 200
        # Upstream incorrectly sets Content-Length for streaming
        mock_response.headers = {
            "content-type": "text/event-stream",
            "content-length": "1234",  # This should NOT be passed through
            "cache-control": "no-cache",
        }

        async def mock_iter_bytes():
            yield b'data: {"id":"msg_1","type":"message_start"}\n\n'
            yield b'data: {"id":"msg_1","type":"content_block_start"}\n\n'
            yield b'data: {"id":"msg_1","type":"content_block_delta","delta":{"text":"Hello"}}\n\n'
            yield b'data: {"id":"msg_1","type":"message_stop"}\n\n'
            yield b"data: [DONE]\n\n"

        mock_response.aiter_bytes = mock_iter_bytes
        mock_response.aread = AsyncMock(return_value=b"")

        mock_stream_context = AsyncMock()
        mock_stream_context.__aenter__.return_value = mock_response
        mock_stream_context.__aexit__.return_value = None

        mock_client.stream.return_value = mock_stream_context

        # Call dispatch_request
        response = await mock_proxy_service.dispatch_request(
            mock_streaming_request, context
        )

        # Verify streaming response
        assert isinstance(response, StreamingResponse)

        # Verify headers do NOT include Content-Length
        assert "content-length" not in response.headers
        assert "Content-Length" not in response.headers

        # Verify expected streaming headers are present
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["connection"] == "keep-alive"
        assert response.headers["x-accel-buffering"] == "no"


@pytest.mark.asyncio
async def test_streaming_response_with_logging(
    mock_proxy_service, mock_streaming_request
):
    """Test that StreamingResponseWithLogging is used and doesn't include Content-Length."""
    auth_manager = MockAuthManager()

    context = HandlerConfig(
        provider_name="codex",
        auth_manager=auth_manager,
        target_base_url="https://chatgpt.com/backend-api/codex",
        supports_streaming=True,
    )

    # Mock the streaming response
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.headers = {
            "content-type": "text/event-stream",
            "content-length": "5678",  # Should be stripped
        }

        async def mock_iter_bytes():
            yield b'data: {"text": "Response"}\n\n'
            yield b"data: [DONE]\n\n"

        mock_response.aiter_bytes = mock_iter_bytes
        mock_response.aread = AsyncMock(return_value=b"")

        mock_stream_context = AsyncMock()
        mock_stream_context.__aenter__.return_value = mock_response
        mock_stream_context.__aexit__.return_value = None

        mock_client.stream.return_value = mock_stream_context

        # Patch StreamingResponseWithLogging to verify it's used
        with patch(
            "ccproxy.services.proxy_service.StreamingResponseWithLogging"
        ) as mock_streaming_response_class:
            # Create a mock response that behaves like StreamingResponseWithLogging
            mock_streaming_response = MagicMock(spec=StreamingResponse)
            mock_streaming_response.media_type = "text/event-stream"
            mock_streaming_response.headers = {
                "cache-control": "no-cache",
                "connection": "keep-alive",
                "x-accel-buffering": "no",
            }
            mock_streaming_response_class.return_value = mock_streaming_response

            # Call dispatch_request
            response = await mock_proxy_service.dispatch_request(
                mock_streaming_request, context
            )

            # Verify StreamingResponseWithLogging was used
            mock_streaming_response_class.assert_called_once()
            call_args = mock_streaming_response_class.call_args

            # Verify the headers passed to StreamingResponseWithLogging
            assert call_args.kwargs["status_code"] == 200
            headers = call_args.kwargs["headers"]
            assert "content-length" not in headers
            assert "Content-Length" not in headers
            assert headers["content-type"] == "text/event-stream"
            assert headers["Cache-Control"] == "no-cache"
            assert headers["Connection"] == "keep-alive"
            assert headers["X-Accel-Buffering"] == "no"


@pytest.mark.asyncio
async def test_non_streaming_response_preserves_headers(
    mock_proxy_service, mock_streaming_request
):
    """Test that non-streaming responses can include Content-Length."""
    auth_manager = MockAuthManager()

    # Make request non-streaming
    mock_streaming_request.body.return_value = (
        b'{"messages": [{"role": "user", "content": "Hello"}], '
        b'"model": "gpt-4", "stream": false}'
    )

    context = HandlerConfig(
        provider_name="codex",
        auth_manager=auth_manager,
        target_base_url="https://chatgpt.com/backend-api/codex",
        supports_streaming=True,
    )

    # Mock the non-streaming response
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        response_content = {"id": "msg_1", "content": "Hello response"}
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.content = json.dumps(response_content).encode()
        mock_response.headers = {
            "content-type": "application/json",
            "content-length": str(len(mock_response.content)),
        }
        mock_response.text = json.dumps(response_content)

        mock_client.request.return_value = mock_response

        # Call dispatch_request
        response = await mock_proxy_service.dispatch_request(
            mock_streaming_request, context
        )

        # For non-streaming responses, Response class is used which
        # automatically calculates Content-Length
        assert response.status_code == 200
        response_body = response.body
        assert isinstance(response_body, bytes)
        body = json.loads(response_body)
        assert body["id"] == "msg_1"
        assert body["content"] == "Hello response"


@pytest.mark.asyncio
async def test_streaming_with_adapter_no_content_length(
    mock_proxy_service, mock_streaming_request
):
    """Test streaming with adapter doesn't include Content-Length."""
    auth_manager = MockAuthManager()

    # Create a mock adapter
    class MockAdapter(APIAdapter):
        async def adapt_request(self, request: dict[str, Any]) -> dict[str, Any]:
            return request

        async def adapt_response(self, response: dict[str, Any]) -> dict[str, Any]:
            return response

        async def adapt_stream(self, stream):
            async for chunk in stream:
                yield chunk

    adapter = MockAdapter()

    context = HandlerConfig(
        provider_name="codex",
        auth_manager=auth_manager,
        target_base_url="https://chatgpt.com/backend-api/codex",
        supports_streaming=True,
        response_adapter=adapter,
    )

    # Mock the streaming response
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.headers = {
            "content-type": "text/event-stream",
            "content-length": "9999",  # Should be stripped even with adapter
        }

        async def mock_iter_bytes():
            yield b'data: {"adapted": true}\n\n'
            yield b"data: [DONE]\n\n"

        mock_response.aiter_bytes = mock_iter_bytes
        mock_response.aread = AsyncMock(return_value=b"")

        mock_stream_context = AsyncMock()
        mock_stream_context.__aenter__.return_value = mock_response
        mock_stream_context.__aexit__.return_value = None

        mock_client.stream.return_value = mock_stream_context

        # Call dispatch_request
        response = await mock_proxy_service.dispatch_request(
            mock_streaming_request, context
        )

        # Verify streaming response without Content-Length
        assert isinstance(response, StreamingResponse)
        assert "content-length" not in response.headers
        assert "Content-Length" not in response.headers
        # Verify content-type is set in headers
        assert response.headers["content-type"] == "text/event-stream"
