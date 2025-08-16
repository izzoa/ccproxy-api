"""Tests for ProviderContext and unified dispatch architecture."""

from typing import Any

import pytest

from ccproxy.adapters.base import APIAdapter
from ccproxy.auth.base import AuthManager
from ccproxy.services.provider_context import ProviderContext


class MockAuthManager(AuthManager):
    """Mock authentication manager for testing."""

    async def get_auth_headers(self) -> dict[str, str]:
        """Get mock auth headers."""
        return {"authorization": "Bearer test-token"}

    async def validate_credentials(self) -> bool:
        """Mock validation always returns True."""
        return True

    def get_provider_name(self) -> str:
        """Get mock provider name."""
        return "mock-provider"


class MockAdapter(APIAdapter):
    """Mock API adapter for testing."""

    async def adapt_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Mock request adaptation."""
        return {"adapted": True, **request}

    async def adapt_response(self, response: dict[str, Any]) -> dict[str, Any]:
        """Mock response adaptation."""
        return {"adapted": True, **response}

    async def adapt_stream(self, stream):
        """Mock stream adaptation."""
        async for chunk in stream:
            yield {"adapted": True, **chunk}


class MockTransformer:
    """Mock transformer that implements the protocol."""

    def transform_headers(
        self, headers: dict[str, str], **kwargs: Any
    ) -> dict[str, str]:
        """Mock header transformer."""
        result = headers.copy()
        result["x-transformed"] = "true"
        return result

    def transform_body(self, body: Any) -> Any:
        """Mock body transformer."""
        return body


@pytest.mark.asyncio
async def test_provider_context_creation():
    """Test ProviderContext can be created with required fields."""
    auth = MockAuthManager()

    context = ProviderContext(
        provider_name="test-provider",
        auth_manager=auth,
        target_base_url="https://api.test.com",
    )

    assert context.provider_name == "test-provider"
    assert context.auth_manager == auth
    assert context.target_base_url == "https://api.test.com"
    assert context.supports_streaming is True  # default
    assert context.requires_session is False  # default
    assert context.extra_headers == {}  # default
    assert context.timeout == 240.0  # default


@pytest.mark.asyncio
async def test_provider_context_with_adapters():
    """Test ProviderContext with request/response adapters."""
    auth = MockAuthManager()
    adapter = MockAdapter()

    context = ProviderContext(
        provider_name="test-provider",
        auth_manager=auth,
        target_base_url="https://api.test.com",
        request_adapter=adapter,
        response_adapter=adapter,
    )

    assert context.request_adapter == adapter
    assert context.response_adapter == adapter


@pytest.mark.asyncio
async def test_provider_context_with_custom_settings():
    """Test ProviderContext with custom settings."""
    auth = MockAuthManager()

    context = ProviderContext(
        provider_name="test-provider",
        auth_manager=auth,
        target_base_url="https://api.test.com",
        session_id="test-session-123",
        account_id="test-account-456",
        timeout=300.0,
        supports_streaming=False,
        requires_session=True,
        extra_headers={"x-custom": "header-value"},
    )

    assert context.session_id == "test-session-123"
    assert context.account_id == "test-account-456"
    assert context.timeout == 300.0
    assert context.supports_streaming is False
    assert context.requires_session is True
    assert context.extra_headers == {"x-custom": "header-value"}


@pytest.mark.asyncio
async def test_auth_manager_interface():
    """Test AuthManager interface methods."""
    auth = MockAuthManager()

    # Test get_auth_headers
    headers = await auth.get_auth_headers()
    assert headers == {"authorization": "Bearer test-token"}

    # Test validate_credentials
    is_valid = await auth.validate_credentials()
    assert is_valid is True

    # Test get_provider_name
    provider_name = auth.get_provider_name()
    assert provider_name == "mock-provider"


def test_provider_context_with_transformer():
    """Test ProviderContext with request transformer."""
    auth = MockAuthManager()
    transformer = MockTransformer()

    context = ProviderContext(
        provider_name="test-provider",
        auth_manager=auth,
        target_base_url="https://api.test.com",
        request_transformer=transformer,
    )

    assert context.request_transformer == transformer

    # Test transformer works
    headers = {"x-original": "value"}
    transformed = context.request_transformer.transform_headers(headers)
    assert transformed == {"x-original": "value", "x-transformed": "true"}


def test_provider_context_defaults():
    """Test ProviderContext uses correct defaults."""
    auth = MockAuthManager()

    context = ProviderContext(
        provider_name="test",
        auth_manager=auth,
        target_base_url="https://api.test.com",
    )

    # Check all defaults
    assert context.request_adapter is None
    assert context.response_adapter is None
    assert context.request_transformer is None
    assert context.session_id is None
    assert context.account_id is None
    assert context.timeout == 240.0
    assert context.supports_streaming is True
    assert context.requires_session is False
    assert context.extra_headers == {}


@pytest.mark.asyncio
async def test_multiple_provider_contexts():
    """Test creating multiple ProviderContext instances for different providers."""
    auth1 = MockAuthManager()
    auth2 = MockAuthManager()

    # Create context for Codex provider
    codex_context = ProviderContext(
        provider_name="codex",
        auth_manager=auth1,
        target_base_url="https://chatgpt.com/backend-api/codex",
        requires_session=True,
        extra_headers={"session_id": "codex-123"},
    )

    # Create context for Claude provider
    claude_context = ProviderContext(
        provider_name="claude",
        auth_manager=auth2,
        target_base_url="https://api.anthropic.com",
        requires_session=False,
    )

    # Verify they are independent
    assert codex_context.provider_name == "codex"
    assert claude_context.provider_name == "claude"
    assert codex_context.requires_session is True
    assert claude_context.requires_session is False
    assert codex_context.extra_headers == {"session_id": "codex-123"}
    assert claude_context.extra_headers == {}
