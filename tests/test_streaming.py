"""Tests for SSE streaming functionality.

Tests streaming responses for both OpenAI and Anthropic API formats,
including proper SSE format compliance, error handling, and stream interruption.
Uses factory fixtures for flexible test configuration and reduced duplication.

The tests cover:
- OpenAI streaming format (/sdk/v1/chat/completions with stream=true)
- Anthropic streaming format (/sdk/v1/messages with stream=true)
- SSE format compliance verification
- Streaming event sequence validation
- Error handling for failed streams
- Content parsing and reconstruction
"""

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from tests.helpers.assertions import assert_sse_format_compliance, assert_sse_headers
from tests.helpers.test_data import (
    STREAMING_ANTHROPIC_REQUEST,
    STREAMING_OPENAI_REQUEST,
)


if TYPE_CHECKING:
    pass


@pytest.mark.unit
def test_openai_streaming_response(
    client_with_mock_claude_streaming: TestClient,
) -> None:
    """Test OpenAI streaming endpoint with proper SSE format."""
    client = client_with_mock_claude_streaming

    # Make streaming request to OpenAI SDK endpoint
    with client.stream(
        "POST", "/sdk/v1/chat/completions", json=STREAMING_OPENAI_REQUEST
    ) as response:
        assert response.status_code == 200
        assert_sse_headers(response)

        # Collect streaming chunks
        chunks: list[str] = []
        for line in response.iter_lines():
            if line.strip():
                chunks.append(line)

        assert_sse_format_compliance(chunks)


@pytest.mark.unit
@pytest.mark.parametrize(
    "endpoint_path,request_data",
    [
        ("/sdk/v1/messages", STREAMING_ANTHROPIC_REQUEST),
        ("/sdk/v1/chat/completions", STREAMING_OPENAI_REQUEST),
    ],
    ids=["anthropic_streaming", "openai_streaming"],
)
def test_streaming_endpoints(
    client_with_mock_claude_streaming: TestClient,
    endpoint_path: str,
    request_data: dict[str, Any],
) -> None:
    """Test streaming endpoints with proper SSE format compliance."""
    client = client_with_mock_claude_streaming

    # Make streaming request
    with client.stream("POST", endpoint_path, json=request_data) as response:
        assert response.status_code == 200
        assert_sse_headers(response)

        # Collect streaming chunks
        chunks: list[str] = []
        for line in response.iter_lines():
            if line.strip():
                chunks.append(line)

        assert_sse_format_compliance(chunks)


@pytest.mark.unit
def test_sse_json_parsing_and_validation(
    client_with_mock_claude_streaming: TestClient,
) -> None:
    """Test that streaming responses contain valid JSON events."""
    client = client_with_mock_claude_streaming

    with client.stream(
        "POST", "/sdk/v1/messages", json=STREAMING_ANTHROPIC_REQUEST
    ) as response:
        assert response.status_code == 200

        # Parse and validate each SSE chunk
        valid_events: list[dict[str, Any]] = []
        for line in response.iter_lines():
            if line.strip() and line.startswith("data: "):
                data_content = line[6:]  # Remove "data: " prefix
                if data_content.strip() != "[DONE]":  # Skip final DONE marker
                    try:
                        event_data: dict[str, Any] = json.loads(data_content)
                        valid_events.append(event_data)
                    except json.JSONDecodeError:
                        pytest.fail(f"Invalid JSON in SSE chunk: {data_content}")

        # Verify we got valid streaming events
        assert len(valid_events) > 0, (
            "Should receive at least one valid streaming event"
        )

        # Check for proper event structure (should have type field)
        for event in valid_events:
            assert isinstance(event, dict), "Each event should be a dictionary"
            assert "type" in event, "Each event should have a 'type' field"


@pytest.mark.unit
def test_streaming_error_handling(
    client_with_unavailable_claude: TestClient,
) -> None:
    """Test streaming endpoint error handling when service is unavailable."""
    client = client_with_unavailable_claude

    # Test streaming request also fails properly
    response = client.post("/sdk/v1/chat/completions", json=STREAMING_OPENAI_REQUEST)
    assert response.status_code == 503

    # Should get service unavailable error instead of streaming response
    response = client.post("/sdk/v1/messages", json=STREAMING_ANTHROPIC_REQUEST)
    assert response.status_code == 503
