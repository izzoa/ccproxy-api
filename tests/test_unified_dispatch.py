"""Integration tests for unified dispatch in ProxyService."""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from ccproxy.adapters.base import APIAdapter
from ccproxy.auth.manager import AuthManager
from ccproxy.services.handler_config import HandlerConfig
from ccproxy.services.proxy_service import ProxyService


class MockAuthManager(AuthManager):
    """Mock authentication manager for testing."""

    def __init__(self, should_fail=False):
        self.should_fail = should_fail

    async def get_access_token(self) -> str:
        """Get mock access token."""
        if self.should_fail:
            from ccproxy.auth.exceptions import AuthenticationError

            raise AuthenticationError("Mock auth failure")
        return "test-token"

    async def get_credentials(self) -> Any:
        """Get mock credentials."""
        if self.should_fail:
            from ccproxy.auth.exceptions import AuthenticationError

            raise AuthenticationError("Mock auth failure")
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
        return not self.should_fail

    async def get_user_profile(self) -> Any:
        """Get mock user profile."""
        return None

    async def get_auth_headers(self) -> dict[str, str]:
        """Get mock auth headers."""
        if self.should_fail:
            from ccproxy.auth.exceptions import AuthenticationError

            raise AuthenticationError("Mock auth failure")
        return {"x-api-key": "test-key"}

    async def validate_credentials(self) -> bool:
        """Mock validation."""
        return not self.should_fail

    def get_provider_name(self) -> str:
        """Get mock provider name."""
        return "mock-provider"

    async def __aenter__(self) -> "MockAuthManager":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        pass


class MockAdapter(APIAdapter):
    """Mock API adapter for testing."""

    async def adapt_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Mock request adaptation."""
        return {"adapted_request": True, **request}

    async def adapt_response(self, response: dict[str, Any]) -> dict[str, Any]:
        """Mock response adaptation."""
        return {"adapted_response": True, **response}

    async def adapt_stream(self, stream):
        """Mock stream adaptation."""
        async for chunk in stream:
            yield {"adapted_stream": True, "chunk": chunk}


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
def mock_request():
    """Create a mock FastAPI Request."""
    request = AsyncMock(spec=Request)
    request.method = "POST"
    request.url.path = "/v1/messages"
    request.url.query = None
    request.headers = {"content-type": "application/json"}
    request.state.request_id = "test-request-123"
    request.state.context = MagicMock()
    request.body.return_value = b'{"messages": [], "model": "claude-3"}'

    return request


@pytest.mark.asyncio
async def test_dispatch_request_success(mock_proxy_service, mock_request):
    """Test successful request dispatch."""
    auth_manager = MockAuthManager()

    context = HandlerConfig(
        provider_name="test",
        auth_manager=auth_manager,
        target_base_url="https://api.test.com",
    )

    # Mock the HTTP response
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.content = b'{"result": "success"}'
        mock_response.headers = {"content-type": "application/json"}
        mock_response.text = '{"result": "success"}'

        mock_client.request.return_value = mock_response

        # Call dispatch_request
        response = await mock_proxy_service.dispatch_request(mock_request, context)

        # Verify response
        assert isinstance(response, Response)
        assert response.status_code == 200
        response_body = response.body
        assert isinstance(response_body, bytes)
        assert json.loads(response_body) == {"result": "success"}


@pytest.mark.asyncio
async def test_dispatch_request_with_adapter(mock_proxy_service, mock_request):
    """Test request dispatch with adapters."""
    auth_manager = MockAuthManager()
    adapter = MockAdapter()

    context = HandlerConfig(
        provider_name="test",
        auth_manager=auth_manager,
        target_base_url="https://api.test.com",
        request_adapter=adapter,
        response_adapter=adapter,
    )

    # Mock the HTTP response
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.content = b'{"result": "success"}'
        mock_response.headers = {"content-type": "application/json"}

        mock_client.request.return_value = mock_response

        # Call dispatch_request
        response = await mock_proxy_service.dispatch_request(mock_request, context)

        # Verify response was adapted
        assert isinstance(response, Response)
        response_body = response.body
        assert isinstance(response_body, bytes)
        body = json.loads(response_body)
        assert body["adapted_response"] is True
        assert body["result"] == "success"


@pytest.mark.asyncio
async def test_dispatch_request_streaming(mock_proxy_service, mock_request):
    """Test streaming request dispatch."""
    auth_manager = MockAuthManager()

    # Modify request to indicate streaming
    mock_request.body.return_value = (
        b'{"messages": [], "model": "claude-3", "stream": true}'
    )

    context = HandlerConfig(
        provider_name="test",
        auth_manager=auth_manager,
        target_base_url="https://api.test.com",
        supports_streaming=True,
    )

    # Mock the streaming response
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        mock_response = AsyncMock()
        mock_response.status_code = 200

        async def mock_iter_bytes():
            yield b'data: {"text": "Hello"}\n\n'
            yield b'data: {"text": " World"}\n\n'
            yield b"data: [DONE]\n\n"

        mock_response.aiter_bytes = mock_iter_bytes
        mock_response.aread = AsyncMock(return_value=b"")

        mock_stream_context = AsyncMock()
        mock_stream_context.__aenter__.return_value = mock_response
        mock_stream_context.__aexit__.return_value = None

        mock_client.stream.return_value = mock_stream_context

        # Call dispatch_request
        response = await mock_proxy_service.dispatch_request(mock_request, context)

        # Verify streaming response
        assert isinstance(response, StreamingResponse)
        # Verify content-type is set in headers (not media_type attribute)
        assert response.headers["content-type"] == "text/event-stream"


@pytest.mark.asyncio
async def test_dispatch_request_auth_failure(mock_proxy_service, mock_request):
    """Test request dispatch with authentication failure."""
    auth_manager = MockAuthManager(should_fail=True)

    context = HandlerConfig(
        provider_name="test",
        auth_manager=auth_manager,
        target_base_url="https://api.test.com",
    )

    # Call dispatch_request and expect HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await mock_proxy_service.dispatch_request(mock_request, context)

    assert exc_info.value.status_code == 401
    assert "Mock auth failure" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_dispatch_request_with_extra_headers(mock_proxy_service, mock_request):
    """Test request dispatch with extra headers."""
    auth_manager = MockAuthManager()

    context = HandlerConfig(
        provider_name="test",
        auth_manager=auth_manager,
        target_base_url="https://api.test.com",
        extra_headers={"x-custom": "header-value", "x-session": "12345"},
    )

    # Mock the HTTP response
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.content = b'{"result": "success"}'
        mock_response.headers = {"content-type": "application/json"}

        mock_client.request.return_value = mock_response

        # Call dispatch_request
        await mock_proxy_service.dispatch_request(mock_request, context)

        # Verify extra headers were included
        call_args = mock_client.request.call_args
        headers = call_args.kwargs["headers"]
        assert headers["x-custom"] == "header-value"
        assert headers["x-session"] == "12345"
        assert headers["x-api-key"] == "test-key"  # Auth header


@pytest.mark.asyncio
async def test_dispatch_request_url_building(mock_proxy_service, mock_request):
    """Test URL building in dispatch_request."""
    auth_manager = MockAuthManager()

    # Test with /v1 prefix removal
    mock_request.url.path = "/v1/chat/completions"

    context = HandlerConfig(
        provider_name="test",
        auth_manager=auth_manager,
        target_base_url="https://api.test.com",
    )

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.content = b"{}"
        mock_response.headers = {}

        mock_client.request.return_value = mock_response

        await mock_proxy_service.dispatch_request(mock_request, context)

        # Verify URL was built correctly (mapped to Anthropic endpoint)
        call_args = mock_client.request.call_args
        assert call_args.kwargs["url"] == "https://api.test.com/v1/messages"


@pytest.mark.asyncio
async def test_dispatch_request_error_response(mock_proxy_service, mock_request):
    """Test error response handling in dispatch_request."""
    auth_manager = MockAuthManager()

    context = HandlerConfig(
        provider_name="test",
        auth_manager=auth_manager,
        target_base_url="https://api.test.com",
    )

    # Mock an error response
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        mock_response = AsyncMock()
        mock_response.status_code = 400
        mock_response.text = "Bad request error"

        mock_client.request.return_value = mock_response

        # Call dispatch_request and expect exception
        with pytest.raises(HTTPException) as exc_info:
            await mock_proxy_service.dispatch_request(mock_request, context)

        assert exc_info.value.status_code == 500
        assert "Request failed with status 400" in str(exc_info.value.detail)
