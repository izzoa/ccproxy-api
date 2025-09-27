"""External OpenAI Codex API mocks using httpx_mock.

These fixtures intercept HTTP calls to chatgpt.com/backend-api/codex for testing ProxyService
and other components that make direct HTTP requests to external APIs.
"""

from typing import Any

import pytest
from pytest_httpx import HTTPXMock


@pytest.fixture
def mock_external_openai_codex_api(
    httpx_mock: HTTPXMock, codex_responses: dict[str, Any]
) -> HTTPXMock:
    """Mock OpenAI Codex API responses for standard completion requests.

    This fixture intercepts HTTP calls to chatgpt.com/backend-api/codex and returns
    mock responses for testing ProxyService and similar components.

    Mocking Strategy: External HTTP interception via httpx_mock
    Use Case: Testing HTTP calls to chatgpt.com/backend-api/codex
    HTTP Calls: Intercepted and mocked

    Args:
        httpx_mock: HTTPXMock fixture for HTTP interception
        codex_responses: Response data fixture

    Returns:
        HTTPXMock configured with Codex API responses
    """
    httpx_mock.add_response(
        url="https://chatgpt.com/backend-api/codex/responses",
        json=codex_responses["standard_completion"],
        status_code=200,
        headers={"content-type": "application/json"},
    )
    return httpx_mock


@pytest.fixture
def mock_external_openai_codex_api_streaming(httpx_mock: HTTPXMock) -> HTTPXMock:
    """Mock OpenAI Codex API streaming responses using SSE format.

    This fixture intercepts HTTP calls to chatgpt.com/backend-api/codex for streaming
    responses and returns SSE-formatted mock data.

    Mocking Strategy: External HTTP interception via httpx_mock
    Use Case: Testing streaming HTTP calls to Codex API
    HTTP Calls: Intercepted and mocked with SSE format

    Args:
        httpx_mock: HTTPXMock fixture for HTTP interception

    Returns:
        HTTPXMock configured with streaming Codex responses
    """
    streaming_response = """data: {"id":"chunk_1","object":"chat.completion.chunk","created":1234567890,"model":"gpt-5","choices":[{"index":0,"delta":{"role":"assistant","content":"Hello"},"finish_reason":null}]}

data: {"id":"chunk_1","object":"chat.completion.chunk","created":1234567890,"model":"gpt-5","choices":[{"index":0,"delta":{"content":" from"},"finish_reason":null}]}

data: {"id":"chunk_1","object":"chat.completion.chunk","created":1234567890,"model":"gpt-5","choices":[{"index":0,"delta":{"content":" Codex!"},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":3,"total_tokens":13}}

data: [DONE]

"""

    httpx_mock.add_response(
        url="https://chatgpt.com/backend-api/codex/responses",
        content=streaming_response.encode(),
        status_code=200,
        headers={
            "content-type": "text/event-stream",
            "cache-control": "no-cache",
            "connection": "keep-alive",
        },
    )
    return httpx_mock


@pytest.fixture
def mock_external_openai_codex_api_error(httpx_mock: HTTPXMock) -> HTTPXMock:
    """Mock OpenAI Codex API error responses.

    This fixture intercepts HTTP calls to chatgpt.com/backend-api/codex and returns
    error responses for testing error handling.

    Mocking Strategy: External HTTP interception via httpx_mock
    Use Case: Testing error scenarios for Codex API calls
    HTTP Calls: Intercepted and mocked with error responses

    Args:
        httpx_mock: HTTPXMock fixture for HTTP interception

    Returns:
        HTTPXMock configured with Codex error responses
    """
    httpx_mock.add_response(
        url="https://chatgpt.com/backend-api/codex/responses",
        json={
            "error": {
                "type": "invalid_request_error",
                "message": "Invalid model specified",
                "code": "invalid_model",
            }
        },
        status_code=400,
        headers={"content-type": "application/json"},
    )
    return httpx_mock


@pytest.fixture
def mock_external_openai_oauth_api(httpx_mock: HTTPXMock) -> HTTPXMock:
    """Mock OpenAI OAuth API responses for authentication.

    This fixture intercepts HTTP calls to OpenAI's OAuth endpoints for
    testing authentication flows.

    Mocking Strategy: External HTTP interception via httpx_mock
    Use Case: Testing OAuth authentication flows
    HTTP Calls: Intercepted and mocked

    Args:
        httpx_mock: HTTPXMock fixture for HTTP interception

    Returns:
        HTTPXMock configured with OAuth responses
    """
    # Mock token endpoint
    httpx_mock.add_response(
        url="https://auth0.openai.com/oauth/token",
        json={
            "access_token": "test-oauth-access-token-12345",
            "refresh_token": "test-oauth-refresh-token-67890",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "model.request model.read organization.read",
        },
        status_code=200,
        headers={"content-type": "application/json"},
    )

    # Mock userinfo endpoint
    httpx_mock.add_response(
        url="https://auth0.openai.com/userinfo",
        json={"sub": "test-user-123", "email": "test@example.com", "name": "Test User"},
        status_code=200,
        headers={"content-type": "application/json"},
    )

    return httpx_mock
