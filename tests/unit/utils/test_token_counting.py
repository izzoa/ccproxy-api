from unittest.mock import MagicMock, patch

import pytest

from ccproxy.utils.token_counting import (
    TokenCounter,
    count_messages_tokens,
    count_tokens,
    get_token_counter,
)


@pytest.fixture
def token_counter():
    return TokenCounter()


def test_count_tokens_approximate():
    counter = TokenCounter()
    counter._tiktoken_available = False

    text = "This is a test message"
    count = counter.count_tokens(text)

    assert count > 0
    assert count == len(text) // 4


def test_count_tokens_with_tiktoken():
    mock_tiktoken = MagicMock()
    mock_encoding = MagicMock()
    mock_encoding.encode.return_value = [1, 2, 3, 4, 5]
    mock_tiktoken.encoding_for_model.return_value = mock_encoding

    counter = TokenCounter()
    counter._tiktoken = mock_tiktoken
    counter._tiktoken_available = True

    text = "This is a test message"
    count = counter.count_tokens(text, model="gpt-4")

    assert count == 5
    mock_tiktoken.encoding_for_model.assert_called_once_with("gpt-4")


def test_count_tokens_tiktoken_fallback():
    mock_tiktoken = MagicMock()
    mock_encoding = MagicMock()
    mock_encoding.encode.return_value = [1, 2, 3]
    mock_tiktoken.encoding_for_model.side_effect = KeyError("Unknown model")
    mock_tiktoken.get_encoding.return_value = mock_encoding

    counter = TokenCounter()
    counter._tiktoken = mock_tiktoken
    counter._tiktoken_available = True

    text = "Test"
    count = counter.count_tokens(text, model="unknown-model")

    assert count == 3
    mock_tiktoken.get_encoding.assert_called_once_with("cl100k_base")


def test_count_messages_tokens_simple(token_counter):
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there"},
    ]

    count = token_counter.count_messages_tokens(messages)

    assert count > 0


def test_count_messages_tokens_with_vision(token_counter):
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What's in this image?"},
                {"type": "image_url", "image_url": {"url": "https://example.com/img.jpg"}},
            ],
        }
    ]

    count = token_counter.count_messages_tokens(messages)

    assert count > 85


def test_count_messages_tokens_with_function_call(token_counter):
    messages = [
        {"role": "user", "content": "Get the weather"},
        {
            "role": "assistant",
            "content": None,
            "function_call": {"name": "get_weather", "arguments": '{"location": "NYC"}'},
        },
    ]

    count = token_counter.count_messages_tokens(messages)

    assert count > 0


def test_count_messages_tokens_with_tool_calls(token_counter):
    messages = [
        {"role": "user", "content": "Get the weather"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"location": "NYC"}'},
                }
            ],
        },
    ]

    count = token_counter.count_messages_tokens(messages)

    assert count > 0


def test_count_anthropic_messages_tokens(token_counter):
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there"},
    ]

    count = token_counter.count_anthropic_messages_tokens(messages, system="You are helpful")

    assert count > 0


def test_count_anthropic_messages_with_tool_use(token_counter):
    messages = [
        {"role": "user", "content": "Get weather"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_123",
                    "name": "get_weather",
                    "input": {"location": "NYC"},
                }
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_123", "content": "Sunny, 72F"}],
        },
    ]

    count = token_counter.count_anthropic_messages_tokens(messages)

    assert count > 0


def test_count_tokens_convenience_function():
    text = "This is a test"
    count = count_tokens(text)

    assert count > 0


def test_count_messages_tokens_convenience_function():
    messages = [{"role": "user", "content": "Hello"}]

    count = count_messages_tokens(messages)

    assert count > 0


def test_count_messages_tokens_anthropic_model():
    messages = [{"role": "user", "content": "Hello"}]

    count = count_messages_tokens(messages, model="claude-3-5-sonnet")

    assert count > 0


def test_get_token_counter_singleton():
    counter1 = get_token_counter()
    counter2 = get_token_counter()

    assert counter1 is counter2


def test_count_messages_with_name_field(token_counter):
    messages = [
        {"role": "user", "name": "Alice", "content": "Hello"},
    ]

    count = token_counter.count_messages_tokens(messages)

    assert count > 0