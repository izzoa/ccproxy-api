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
    """Test ProviderContext can be created with simplified fields."""
    context = ProviderContext()

    # Check defaults
    assert context.request_adapter is None
    assert context.response_adapter is None
    assert context.request_transformer is None
    assert context.response_transformer is None
    assert context.supports_streaming is True  # default


@pytest.mark.asyncio
async def test_provider_context_with_adapters():
    """Test ProviderContext with request/response adapters."""
    adapter = MockAdapter()

    context = ProviderContext(
        request_adapter=adapter,
        response_adapter=adapter,
    )

    assert context.request_adapter == adapter
    assert context.response_adapter == adapter


@pytest.mark.asyncio
async def test_provider_context_with_custom_settings():
    """Test ProviderContext with custom settings."""
    adapter = MockAdapter()
    transformer = MockTransformer()

    context = ProviderContext(
        request_adapter=adapter,
        response_adapter=adapter,
        request_transformer=transformer,
        response_transformer=transformer,
        supports_streaming=False,
    )

    assert context.request_adapter == adapter
    assert context.response_adapter == adapter
    assert context.request_transformer == transformer
    assert context.response_transformer == transformer
    assert context.supports_streaming is False


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
    transformer = MockTransformer()

    context = ProviderContext(
        request_transformer=transformer,
    )

    assert context.request_transformer == transformer

    # Test transformer works
    headers = {"x-original": "value"}
    transformed = context.request_transformer.transform_headers(headers)
    assert transformed == {"x-original": "value", "x-transformed": "true"}


def test_provider_context_defaults():
    """Test ProviderContext uses correct defaults."""
    context = ProviderContext()

    # Check all defaults
    assert context.request_adapter is None
    assert context.response_adapter is None
    assert context.request_transformer is None
    assert context.response_transformer is None
    assert context.supports_streaming is True


@pytest.mark.asyncio
async def test_multiple_provider_contexts():
    """Test creating multiple ProviderContext instances."""
    adapter1 = MockAdapter()
    adapter2 = MockAdapter()
    transformer1 = MockTransformer()
    transformer2 = MockTransformer()

    # Create context with streaming enabled
    streaming_context = ProviderContext(
        request_adapter=adapter1,
        response_adapter=adapter1,
        request_transformer=transformer1,
        supports_streaming=True,
    )

    # Create context with streaming disabled
    non_streaming_context = ProviderContext(
        request_adapter=adapter2,
        response_adapter=adapter2,
        request_transformer=transformer2,
        supports_streaming=False,
    )

    # Verify they are independent
    assert streaming_context.request_adapter == adapter1
    assert non_streaming_context.request_adapter == adapter2
    assert streaming_context.supports_streaming is True
    assert non_streaming_context.supports_streaming is False


def test_provider_context_is_immutable():
    """Test that ProviderContext is immutable (frozen dataclass)."""
    from dataclasses import FrozenInstanceError

    context = ProviderContext(supports_streaming=True)

    # Attempting to modify should raise FrozenInstanceError
    with pytest.raises(FrozenInstanceError):
        context.supports_streaming = False  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        context.request_adapter = MockAdapter()  # type: ignore[misc]
