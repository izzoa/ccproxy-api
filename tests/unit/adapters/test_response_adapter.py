"""Tests for ResponseAdapter - Codex/OpenAI compatibility layer."""

import json
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccproxy.adapters.openai.response_adapter import ResponseAdapter
from ccproxy.models.messages import Message, ToolCall, ToolChoice
from ccproxy.models.requests import ChatRequest
from ccproxy.models.responses import ChatResponse, Choice, Usage
from ccproxy.services.model_info_service import ModelInfo


class TestResponseAdapter:
    """Test suite for ResponseAdapter functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.adapter = ResponseAdapter()
        self.mock_model_info_service = AsyncMock()

    # ==================== Tool/Function Calling Tests ====================

    @pytest.mark.asyncio
    async def test_chat_to_response_request_with_tools(self):
        """Test conversion of chat request with tools to Response API format."""
        # Create chat request with tools
        chat_request = ChatRequest(
            model="gpt-4o",
            messages=[
                Message(role="system", content="You are a helpful assistant"),
                Message(role="user", content="What's the weather?"),
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Get weather for a city",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "city": {"type": "string", "description": "City name"},
                            },
                            "required": ["city"],
                        },
                    },
                }
            ],
            tool_choice="auto",
            stream=True,
        )

        # Convert to Response API
        result = await self.adapter.chat_to_response_request(
            chat_request, model_info_service=self.mock_model_info_service
        )

        # Verify tools are included in messages
        assert len(result["messages"]) == 3  # system + user + tools
        tools_message = result["messages"][-1]
        assert tools_message["role"] == "system"
        assert "get_weather" in tools_message["content"]
        assert "Get weather for a city" in tools_message["content"]

    @pytest.mark.asyncio
    async def test_chat_to_response_request_with_tool_calls(self):
        """Test handling of assistant messages with tool calls."""
        chat_request = ChatRequest(
            model="gpt-4o",
            messages=[
                Message(role="user", content="What's the weather in Paris?"),
                Message(
                    role="assistant",
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="call_123",
                            type="function",
                            function={
                                "name": "get_weather",
                                "arguments": '{"city": "Paris"}',
                            },
                        )
                    ],
                ),
                Message(
                    role="tool",
                    content="Sunny, 22°C",
                    tool_call_id="call_123",
                ),
            ],
            stream=False,
        )

        result = await self.adapter.chat_to_response_request(
            chat_request, model_info_service=self.mock_model_info_service
        )

        # Check tool call is converted to content
        assistant_msg = result["messages"][1]
        assert assistant_msg["role"] == "assistant"
        assert "get_weather" in assistant_msg["content"]
        assert "Paris" in assistant_msg["content"]

        # Check tool response is included
        tool_msg = result["messages"][2]
        assert tool_msg["role"] == "user"
        assert "Sunny, 22°C" in tool_msg["content"]

    @pytest.mark.asyncio
    async def test_chat_to_response_request_with_response_format(self):
        """Test handling of response_format (JSON schema)."""
        chat_request = ChatRequest(
            model="gpt-4o",
            messages=[Message(role="user", content="Generate a person object")],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "person",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "age": {"type": "number"},
                        },
                        "required": ["name", "age"],
                    },
                },
            },
        )

        result = await self.adapter.chat_to_response_request(
            chat_request, model_info_service=self.mock_model_info_service
        )

        # Check schema is injected into messages
        assert len(result["messages"]) == 2
        schema_msg = result["messages"][0]
        assert schema_msg["role"] == "system"
        assert "JSON" in schema_msg["content"]
        assert "person" in schema_msg["content"]

    @pytest.mark.asyncio
    async def test_chat_to_response_request_with_reasoning(self):
        """Test handling of reasoning output request."""
        chat_request = ChatRequest(
            model="gpt-4o",
            messages=[Message(role="user", content="Solve this problem")],
            include_reasoning=True,
        )

        result = await self.adapter.chat_to_response_request(
            chat_request, model_info_service=self.mock_model_info_service
        )

        # Check reasoning instruction is added
        assert len(result["messages"]) >= 1
        # Look for reasoning instructions in messages
        messages_str = json.dumps(result["messages"])
        assert "reasoning" in messages_str.lower() or "thinking" in messages_str.lower()

    # ==================== Streaming Conversion Tests ====================

    async def _create_mock_stream(
        self, chunks: list[dict]
    ) -> AsyncGenerator[dict, None]:
        """Helper to create async generator for testing."""
        for chunk in chunks:
            yield chunk

    @pytest.mark.asyncio
    async def test_stream_response_to_chat_with_content(self):
        """Test streaming conversion with content chunks."""
        stream_chunks = [
            {"delta": {"content": "Hello"}},
            {"delta": {"content": " world"}},
            {"delta": {"content": "!"}},
        ]

        result_chunks = []
        async for chunk in self.adapter.stream_response_to_chat(
            self._create_mock_stream(stream_chunks), "gpt-4o"
        ):
            result_chunks.append(chunk)

        # Verify chunks are properly converted
        assert len(result_chunks) == 3
        assert result_chunks[0]["choices"][0]["delta"]["content"] == "Hello"
        assert result_chunks[1]["choices"][0]["delta"]["content"] == " world"
        assert result_chunks[2]["choices"][0]["delta"]["content"] == "!"

    @pytest.mark.asyncio
    async def test_stream_response_to_chat_with_tool_calls(self):
        """Test streaming conversion with tool call chunks."""
        stream_chunks = [
            {
                "delta": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_456",
                            "type": "function",
                            "function": {"name": "get_weather", "arguments": '{"ci'},
                        }
                    ],
                }
            },
            {
                "delta": {
                    "content": None,
                    "tool_calls": [
                        {"function": {"arguments": 'ty": "London"}'}}
                    ],
                }
            },
        ]

        result_chunks = []
        async for chunk in self.adapter.stream_response_to_chat(
            self._create_mock_stream(stream_chunks), "gpt-4o"
        ):
            result_chunks.append(chunk)

        # Verify tool calls are preserved
        assert len(result_chunks) == 2
        first_chunk = result_chunks[0]["choices"][0]["delta"]
        assert "tool_calls" in first_chunk
        assert first_chunk["tool_calls"][0]["function"]["name"] == "get_weather"

    @pytest.mark.asyncio
    async def test_stream_response_to_chat_with_reasoning(self):
        """Test streaming conversion with reasoning content."""
        stream_chunks = [
            {"delta": {"reasoning_content": "Let me think..."}},
            {"delta": {"reasoning_content": " The answer is"}},
            {"delta": {"content": " 42"}},
        ]

        result_chunks = []
        async for chunk in self.adapter.stream_response_to_chat(
            self._create_mock_stream(stream_chunks), "gpt-4o"
        ):
            result_chunks.append(chunk)

        # Verify reasoning is converted to content with tag
        assert len(result_chunks) == 3
        # Reasoning chunks should be wrapped with tags
        assert "<reasoning>" in result_chunks[0]["choices"][0]["delta"]["content"]
        assert "Let me think..." in result_chunks[0]["choices"][0]["delta"]["content"]

    @pytest.mark.asyncio
    async def test_stream_response_to_chat_with_usage(self):
        """Test streaming conversion with usage data."""
        stream_chunks = [
            {"delta": {"content": "Test"}},
            {
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "total_tokens": 15,
                }
            },
        ]

        result_chunks = []
        async for chunk in self.adapter.stream_response_to_chat(
            self._create_mock_stream(stream_chunks), "gpt-4o"
        ):
            result_chunks.append(chunk)

        # Verify usage is included
        assert len(result_chunks) == 2
        assert "usage" in result_chunks[1]
        assert result_chunks[1]["usage"]["prompt_tokens"] == 10
        assert result_chunks[1]["usage"]["completion_tokens"] == 5
        assert result_chunks[1]["usage"]["total_tokens"] == 15

    # ==================== Non-Streaming Conversion Tests ====================

    @pytest.mark.asyncio
    async def test_response_to_chat_with_content(self):
        """Test non-streaming conversion with regular content."""
        response = {
            "id": "resp_123",
            "model": "gpt-4o",
            "choices": [
                {
                    "message": {"role": "assistant", "content": "Hello!"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "input_tokens": 5,
                "output_tokens": 2,
                "total_tokens": 7,
            },
        }

        result = await self.adapter.response_to_chat(response, "gpt-4o")

        assert result["id"] == "resp_123"
        assert result["model"] == "gpt-4o"
        assert result["choices"][0]["message"]["content"] == "Hello!"
        assert result["usage"]["prompt_tokens"] == 5
        assert result["usage"]["completion_tokens"] == 2

    @pytest.mark.asyncio
    async def test_response_to_chat_with_tool_calls(self):
        """Test non-streaming conversion with tool calls."""
        response = {
            "id": "resp_456",
            "model": "gpt-4o",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_789",
                                "type": "function",
                                "function": {
                                    "name": "calculate",
                                    "arguments": '{"x": 5, "y": 10}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }

        result = await self.adapter.response_to_chat(response, "gpt-4o")

        assert result["choices"][0]["message"]["tool_calls"] is not None
        tool_call = result["choices"][0]["message"]["tool_calls"][0]
        assert tool_call["id"] == "call_789"
        assert tool_call["function"]["name"] == "calculate"

    @pytest.mark.asyncio
    async def test_response_to_chat_with_reasoning(self):
        """Test non-streaming conversion with reasoning content."""
        response = {
            "id": "resp_789",
            "model": "gpt-4o",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "The answer is 42",
                        "reasoning_content": "Let me work through this step by step...",
                    },
                    "finish_reason": "stop",
                }
            ],
        }

        result = await self.adapter.response_to_chat(response, "gpt-4o")

        # Reasoning should be prepended to content
        content = result["choices"][0]["message"]["content"]
        assert "Let me work through this" in content
        assert "The answer is 42" in content

    # ==================== Model Mapping Tests ====================

    def test_map_to_response_api_model(self):
        """Test model name mapping from OpenAI to Response API."""
        # Test direct mappings
        assert self.adapter._map_to_response_api_model("gpt-4o") == "gpt-4o"
        assert self.adapter._map_to_response_api_model("gpt-4o-mini") == "gpt-4o-mini"
        assert self.adapter._map_to_response_api_model("o1") == "o1"
        assert self.adapter._map_to_response_api_model("o1-mini") == "o1-mini"
        assert self.adapter._map_to_response_api_model("o1-preview") == "o1-preview"
        assert self.adapter._map_to_response_api_model("o3-mini") == "o3-mini"

        # Test fallback for unknown models
        assert self.adapter._map_to_response_api_model("gpt-3.5-turbo") == "gpt-4o-mini"
        assert self.adapter._map_to_response_api_model("unknown-model") == "gpt-4o"

    # ==================== Parameter Propagation Tests ====================

    @pytest.mark.asyncio
    async def test_parameter_propagation(self):
        """Test that OpenAI parameters are properly propagated."""
        chat_request = ChatRequest(
            model="gpt-4o",
            messages=[Message(role="user", content="Test")],
            temperature=0.7,
            top_p=0.9,
            max_tokens=100,
            presence_penalty=0.5,
            frequency_penalty=0.3,
            logit_bias={42: 10},
            seed=12345,
        )

        result = await self.adapter.chat_to_response_request(
            chat_request, model_info_service=self.mock_model_info_service
        )

        # Check parameters are included
        assert result["temperature"] == 0.7
        assert result["top_p"] == 0.9
        assert result["max_output_tokens"] == 100
        # Note: Some params may be in metadata or not directly supported
        assert "metadata" in result or "presence_penalty" not in result

    @pytest.mark.asyncio
    async def test_dynamic_model_info_integration(self):
        """Test integration with ModelInfoService for dynamic limits."""
        # Mock model info
        mock_info = ModelInfo(
            name="gpt-4o",
            display_name="GPT-4 Optimized",
            context_window=128000,
            max_output_tokens=4096,
            supports_tools=True,
            supports_vision=True,
        )
        self.mock_model_info_service.get_model_info.return_value = mock_info

        chat_request = ChatRequest(
            model="gpt-4o",
            messages=[Message(role="user", content="Test")],
        )

        result = await self.adapter.chat_to_response_request(
            chat_request, model_info_service=self.mock_model_info_service
        )

        # Verify model info was fetched
        self.mock_model_info_service.get_model_info.assert_called_once_with("gpt-4o")
        
        # Check that max tokens defaults to model's limit if not specified
        if "max_output_tokens" in result:
            assert result["max_output_tokens"] <= 4096

    @pytest.mark.asyncio
    async def test_image_handling(self):
        """Test handling of image content in messages."""
        chat_request = ChatRequest(
            model="gpt-4o",
            messages=[
                Message(
                    role="user",
                    content=[
                        {"type": "text", "text": "What's in this image?"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/jpeg;base64,abc123"},
                        },
                    ],
                )
            ],
        )

        result = await self.adapter.chat_to_response_request(
            chat_request, model_info_service=self.mock_model_info_service
        )

        # Check image content is handled
        user_msg = result["messages"][0]
        assert user_msg["role"] == "user"
        # Image should be converted to text description or preserved
        assert "image" in str(user_msg["content"]).lower()

    @pytest.mark.asyncio
    async def test_system_message_handling(self):
        """Test proper handling of system messages."""
        chat_request = ChatRequest(
            model="gpt-4o",
            messages=[
                Message(role="system", content="You are a pirate"),
                Message(role="user", content="Hello"),
                Message(role="assistant", content="Ahoy!"),
                Message(role="user", content="How are you?"),
            ],
        )

        result = await self.adapter.chat_to_response_request(
            chat_request, model_info_service=self.mock_model_info_service
        )

        # Check message roles are preserved or properly converted
        assert len(result["messages"]) == 4
        assert result["messages"][0]["role"] == "system"
        assert result["messages"][1]["role"] == "user"
        assert result["messages"][2]["role"] == "assistant"
        assert result["messages"][3]["role"] == "user"

    @pytest.mark.asyncio
    async def test_error_handling_in_stream(self):
        """Test error handling during streaming conversion."""

        async def error_stream():
            yield {"delta": {"content": "Start"}}
            raise ValueError("Stream error")

        result_chunks = []
        with pytest.raises(ValueError, match="Stream error"):
            async for chunk in self.adapter.stream_response_to_chat(
                error_stream(), "gpt-4o"
            ):
                result_chunks.append(chunk)

        # Should have received at least the first chunk
        assert len(result_chunks) == 1
        assert result_chunks[0]["choices"][0]["delta"]["content"] == "Start"


class TestResponseAdapterEdgeCases:
    """Test edge cases and error conditions for ResponseAdapter."""

    def setup_method(self):
        """Set up test fixtures."""
        self.adapter = ResponseAdapter()

    @pytest.mark.asyncio
    async def test_empty_messages(self):
        """Test handling of empty message list."""
        chat_request = ChatRequest(
            model="gpt-4o",
            messages=[],
        )

        result = await self.adapter.chat_to_response_request(chat_request)
        assert result["messages"] == []

    @pytest.mark.asyncio
    async def test_none_content_in_messages(self):
        """Test handling of None content in messages."""
        chat_request = ChatRequest(
            model="gpt-4o",
            messages=[
                Message(role="assistant", content=None),
            ],
        )

        result = await self.adapter.chat_to_response_request(chat_request)
        # Should handle None content gracefully
        assert result["messages"][0]["content"] == ""

    @pytest.mark.asyncio
    async def test_malformed_tool_arguments(self):
        """Test handling of malformed JSON in tool arguments."""
        chat_request = ChatRequest(
            model="gpt-4o",
            messages=[
                Message(
                    role="assistant",
                    tool_calls=[
                        ToolCall(
                            id="call_bad",
                            type="function",
                            function={
                                "name": "test",
                                "arguments": "not valid json{",
                            },
                        )
                    ],
                )
            ],
        )

        result = await self.adapter.chat_to_response_request(chat_request)
        # Should handle gracefully without crashing
        assert len(result["messages"]) == 1

    @pytest.mark.asyncio
    async def test_mixed_content_types(self):
        """Test handling of mixed string and list content."""
        chat_request = ChatRequest(
            model="gpt-4o",
            messages=[
                Message(role="user", content="Simple string"),
                Message(
                    role="user",
                    content=[
                        {"type": "text", "text": "Complex"},
                        {"type": "text", "text": " content"},
                    ],
                ),
            ],
        )

        result = await self.adapter.chat_to_response_request(chat_request)
        assert len(result["messages"]) == 2
        assert "Simple string" in result["messages"][0]["content"]
        assert "Complex content" in result["messages"][1]["content"]