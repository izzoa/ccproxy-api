"""Tests for Codex instruction injection modes."""

import json
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import Response

from ccproxy.config.codex import CodexSettings
from ccproxy.core.codex_transformers import CodexRequestTransformer
from ccproxy.models.detection import CodexCacheData, CodexHeaders, CodexInstructionsData


class TestCodexInstructionModes:
    """Test suite for different instruction injection modes."""

    def setup_method(self):
        """Set up test fixtures."""
        self.transformer = CodexRequestTransformer()
        self.mock_detection_data = CodexCacheData(
            codex_version="2024.1.0",
            headers=CodexHeaders(
                session_id="session_123",
                originator="codex_cli_rs",
            ),
            instructions=CodexInstructionsData(
                instructions_field="You are ChatGPT powered by {model}",
            ),
            cached_at=datetime.now(),
        )

    @pytest.mark.asyncio
    async def test_instruction_mode_override(self):
        """Test 'override' mode - replaces system prompt completely."""
        settings = {"system_prompt_injection_mode": "override"}
        
        body = {
            "messages": [
                {"role": "system", "content": "You are a helpful assistant"},
                {"role": "user", "content": "Hello"},
            ],
            "model": "gpt-4o",
        }
        
        result = await self.transformer.transform_codex_request(
            method="POST",
            path="/v1/chat/completions",
            headers={},
            body=body,
            context=settings,
            codex_detection_data=self.mock_detection_data,
            target_base_url="https://api.openai.com",
        )
        
        # Check that system prompt was replaced
        transformed_body = json.loads(result.content)
        assert len(transformed_body["messages"]) == 2
        system_msg = transformed_body["messages"][0]
        assert system_msg["role"] == "system"
        assert "You are ChatGPT powered by gpt-4o" == system_msg["content"]
        assert "helpful assistant" not in system_msg["content"]

    @pytest.mark.asyncio
    async def test_instruction_mode_append(self):
        """Test 'append' mode - appends to existing system prompt."""
        settings = {"system_prompt_injection_mode": "append"}
        
        body = {
            "messages": [
                {"role": "system", "content": "You are a helpful assistant"},
                {"role": "user", "content": "Hello"},
            ],
            "model": "gpt-4o",
        }
        
        result = await self.transformer.transform_codex_request(
            method="POST",
            path="/v1/chat/completions",
            headers={},
            body=body,
            context=settings,
            codex_detection_data=self.mock_detection_data,
            target_base_url="https://api.openai.com",
        )
        
        # Check that system prompt was appended
        transformed_body = json.loads(result.content)
        assert len(transformed_body["messages"]) == 2
        system_msg = transformed_body["messages"][0]
        assert system_msg["role"] == "system"
        assert "You are a helpful assistant" in system_msg["content"]
        assert "You are ChatGPT powered by gpt-4o" in system_msg["content"]

    @pytest.mark.asyncio
    async def test_instruction_mode_disabled(self):
        """Test 'disabled' mode - no injection happens."""
        settings = {"system_prompt_injection_mode": "disabled"}
        
        body = {
            "messages": [
                {"role": "system", "content": "You are a helpful assistant"},
                {"role": "user", "content": "Hello"},
            ],
            "model": "gpt-4o",
        }
        
        result = await self.transformer.transform_codex_request(
            method="POST",
            path="/v1/chat/completions",
            headers={},
            body=body,
            context=settings,
            codex_detection_data=self.mock_detection_data,
            target_base_url="https://api.openai.com",
        )
        
        # Check that system prompt was NOT modified
        transformed_body = json.loads(result.content)
        assert len(transformed_body["messages"]) == 2
        system_msg = transformed_body["messages"][0]
        assert system_msg["role"] == "system"
        assert system_msg["content"] == "You are a helpful assistant"
        assert "ChatGPT" not in system_msg["content"]

    @pytest.mark.asyncio
    async def test_instruction_mode_no_system_message(self):
        """Test injection when there's no existing system message."""
        settings = {"system_prompt_injection_mode": "override"}
        
        body = {
            "messages": [
                {"role": "user", "content": "Hello"},
            ],
            "model": "gpt-4o",
        }
        
        result = await self.transformer.transform_codex_request(
            method="POST",
            path="/v1/chat/completions",
            headers={},
            body=body,
            context=settings,
            codex_detection_data=self.mock_detection_data,
            target_base_url="https://api.openai.com",
        )
        
        # Check that system message was added
        transformed_body = json.loads(result.content)
        assert len(transformed_body["messages"]) == 2
        system_msg = transformed_body["messages"][0]
        assert system_msg["role"] == "system"
        assert "You are ChatGPT powered by gpt-4o" == system_msg["content"]

    @pytest.mark.asyncio
    async def test_instruction_mode_no_system_message_disabled(self):
        """Test disabled mode when there's no existing system message."""
        settings = {"system_prompt_injection_mode": "disabled"}
        
        body = {
            "messages": [
                {"role": "user", "content": "Hello"},
            ],
            "model": "gpt-4o",
        }
        
        result = await self.transformer.transform_codex_request(
            method="POST",
            path="/v1/chat/completions",
            headers={},
            body=body,
            context=settings,
            codex_detection_data=self.mock_detection_data,
            target_base_url="https://api.openai.com",
        )
        
        # Check that NO system message was added
        transformed_body = json.loads(result.content)
        assert len(transformed_body["messages"]) == 1
        assert transformed_body["messages"][0]["role"] == "user"

    @pytest.mark.asyncio
    async def test_instruction_template_with_model_substitution(self):
        """Test that {model} placeholder is correctly substituted."""
        settings = {"system_prompt_injection_mode": "override"}
        
        # Test with different models
        models = ["gpt-4o", "gpt-4o-mini", "o1-mini", "o1-preview"]
        
        for model in models:
            body = {
                "messages": [
                    {"role": "user", "content": "Hello"},
                ],
                "model": model,
            }
            
            result = await self.transformer.transform_codex_request(
                method="POST",
                path="/v1/chat/completions",
                headers={},
                body=body,
                context=settings,
                codex_detection_data=self.mock_detection_data,
                target_base_url="https://api.openai.com",
            )
            
            transformed_body = json.loads(result.content)
            system_msg = transformed_body["messages"][0]
            assert f"You are ChatGPT powered by {model}" == system_msg["content"]

    @pytest.mark.asyncio
    async def test_instruction_mode_with_multiple_system_messages(self):
        """Test handling of multiple system messages."""
        settings = {"system_prompt_injection_mode": "override"}
        
        body = {
            "messages": [
                {"role": "system", "content": "First system prompt"},
                {"role": "system", "content": "Second system prompt"},
                {"role": "user", "content": "Hello"},
            ],
            "model": "gpt-4o",
        }
        
        result = await self.transformer.transform_codex_request(
            method="POST",
            path="/v1/chat/completions",
            headers={},
            body=body,
            context=settings,
            codex_detection_data=self.mock_detection_data,
            target_base_url="https://api.openai.com",
        )
        
        # Check that only the first system message is modified
        transformed_body = json.loads(result.content)
        assert len(transformed_body["messages"]) == 3
        assert transformed_body["messages"][0]["content"] == "You are ChatGPT powered by gpt-4o"
        assert transformed_body["messages"][1]["content"] == "Second system prompt"

    @pytest.mark.asyncio
    async def test_instruction_mode_append_with_multiple_system(self):
        """Test append mode with multiple system messages."""
        settings = {"system_prompt_injection_mode": "append"}
        
        body = {
            "messages": [
                {"role": "system", "content": "First prompt"},
                {"role": "system", "content": "Second prompt"},
                {"role": "user", "content": "Hello"},
            ],
            "model": "gpt-4o",
        }
        
        result = await self.transformer.transform_codex_request(
            method="POST",
            path="/v1/chat/completions",
            headers={},
            body=body,
            context=settings,
            codex_detection_data=self.mock_detection_data,
            target_base_url="https://api.openai.com",
        )
        
        transformed_body = json.loads(result.content)
        # First system message should be appended
        assert "First prompt" in transformed_body["messages"][0]["content"]
        assert "You are ChatGPT powered by gpt-4o" in transformed_body["messages"][0]["content"]
        # Second should remain unchanged
        assert transformed_body["messages"][1]["content"] == "Second prompt"

    @pytest.mark.asyncio
    async def test_instruction_mode_with_empty_template(self):
        """Test handling when instruction template is empty."""
        detection_data = CodexCacheData(
            codex_version="2024.1.0",
            headers=CodexHeaders(session_id="test", originator="test"),
            instructions=CodexInstructionsData(
                instructions_field="",  # Empty template
            ),
            cached_at=datetime.now(),
        )
        
        settings = {"system_prompt_injection_mode": "override"}
        
        body = {
            "messages": [
                {"role": "system", "content": "Original prompt"},
                {"role": "user", "content": "Hello"},
            ],
            "model": "gpt-4o",
        }
        
        result = await self.transformer.transform_codex_request(
            method="POST",
            path="/v1/chat/completions",
            headers={},
            body=body,
            context=settings,
            codex_detection_data=detection_data,
            target_base_url="https://api.openai.com",
        )
        
        # Should not modify when template is empty
        transformed_body = json.loads(result.content)
        assert transformed_body["messages"][0]["content"] == "Original prompt"

    @pytest.mark.asyncio
    async def test_instruction_mode_with_none_template(self):
        """Test handling when instruction template is None."""
        detection_data = CodexCacheData(
            codex_version="2024.1.0",
            headers=CodexHeaders(session_id="test", originator="test"),
            instructions=CodexInstructionsData(
                instructions_field=None,  # None template
            ),
            cached_at=datetime.now(),
        )
        
        settings = {"system_prompt_injection_mode": "override"}
        
        body = {
            "messages": [
                {"role": "system", "content": "Original prompt"},
                {"role": "user", "content": "Hello"},
            ],
            "model": "gpt-4o",
        }
        
        result = await self.transformer.transform_codex_request(
            method="POST",
            path="/v1/chat/completions",
            headers={},
            body=body,
            context=settings,
            codex_detection_data=detection_data,
            target_base_url="https://api.openai.com",
        )
        
        # Should not modify when template is None
        transformed_body = json.loads(result.content)
        assert transformed_body["messages"][0]["content"] == "Original prompt"

    @pytest.mark.asyncio
    async def test_instruction_mode_invalid_value(self):
        """Test handling of invalid injection mode."""
        settings = {"system_prompt_injection_mode": "invalid_mode"}
        
        body = {
            "messages": [
                {"role": "system", "content": "Original"},
                {"role": "user", "content": "Hello"},
            ],
            "model": "gpt-4o",
        }
        
        # Should default to safe behavior (no modification)
        result = await self.transformer.transform_codex_request(
            method="POST",
            path="/v1/chat/completions",
            headers={},
            body=body,
            context=settings,
            codex_detection_data=self.mock_detection_data,
            target_base_url="https://api.openai.com",
        )
        
        transformed_body = json.loads(result.content)
        # Should not modify with invalid mode
        assert transformed_body["messages"][0]["content"] == "Original"

    @pytest.mark.asyncio
    async def test_instruction_mode_preserve_other_messages(self):
        """Test that non-system messages are preserved correctly."""
        settings = {"system_prompt_injection_mode": "override"}
        
        body = {
            "messages": [
                {"role": "system", "content": "System"},
                {"role": "user", "content": "Question 1"},
                {"role": "assistant", "content": "Answer 1"},
                {"role": "user", "content": "Question 2"},
            ],
            "model": "gpt-4o",
        }
        
        result = await self.transformer.transform_codex_request(
            method="POST",
            path="/v1/chat/completions",
            headers={},
            body=body,
            context=settings,
            codex_detection_data=self.mock_detection_data,
            target_base_url="https://api.openai.com",
        )
        
        transformed_body = json.loads(result.content)
        assert len(transformed_body["messages"]) == 4
        # System message modified
        assert "ChatGPT" in transformed_body["messages"][0]["content"]
        # Other messages unchanged
        assert transformed_body["messages"][1]["content"] == "Question 1"
        assert transformed_body["messages"][2]["content"] == "Answer 1"
        assert transformed_body["messages"][3]["content"] == "Question 2"

    @pytest.mark.asyncio
    async def test_instruction_with_complex_template(self):
        """Test instruction with complex template containing special characters."""
        detection_data = CodexCacheData(
            codex_version="2024.1.0",
            headers=CodexHeaders(session_id="test", originator="test"),
            instructions=CodexInstructionsData(
                instructions_field="You are ChatGPT\nModel: {model}\nVersion: 4.0\nCapabilities: [text, code, math]",
            ),
            cached_at=datetime.now(),
        )
        
        settings = {"system_prompt_injection_mode": "override"}
        
        body = {
            "messages": [
                {"role": "user", "content": "Hello"},
            ],
            "model": "gpt-4o",
        }
        
        result = await self.transformer.transform_codex_request(
            method="POST",
            path="/v1/chat/completions",
            headers={},
            body=body,
            context=settings,
            codex_detection_data=detection_data,
            target_base_url="https://api.openai.com",
        )
        
        transformed_body = json.loads(result.content)
        system_msg = transformed_body["messages"][0]
        assert "Model: gpt-4o" in system_msg["content"]
        assert "Version: 4.0" in system_msg["content"]
        assert "Capabilities: [text, code, math]" in system_msg["content"]


class TestCodexInstructionModesIntegration:
    """Integration tests for instruction modes with full request flow."""

    @pytest.mark.asyncio
    async def test_settings_integration(self):
        """Test that settings are properly passed through the system."""
        from ccproxy.config.codex import CodexSettings
        
        # Create settings with specific mode
        settings = CodexSettings(
            enabled=True,
            system_prompt_injection_mode="append",
            base_url="https://api.openai.com",
        )
        
        assert settings.system_prompt_injection_mode == "append"

    @pytest.mark.asyncio
    async def test_mode_switching(self):
        """Test dynamic switching between modes."""
        transformer = CodexRequestTransformer()
        detection_data = CodexCacheData(
            codex_version="2024.1.0",
            headers=CodexHeaders(session_id="test", originator="test"),
            instructions=CodexInstructionsData(
                instructions_field="Injected: {model}",
            ),
            cached_at=datetime.now(),
        )
        
        body = {
            "messages": [
                {"role": "system", "content": "Original"},
                {"role": "user", "content": "Test"},
            ],
            "model": "gpt-4o",
        }
        
        # Test each mode sequentially
        modes = ["override", "append", "disabled"]
        expected_results = [
            "Injected: gpt-4o",  # override replaces
            "Original\n\nInjected: gpt-4o",  # append adds
            "Original",  # disabled keeps original
        ]
        
        for mode, expected in zip(modes, expected_results):
            settings = {"system_prompt_injection_mode": mode}
            
            result = await transformer.transform_codex_request(
                method="POST",
                path="/v1/chat/completions",
                headers={},
                body=body.copy(),
                context=settings,
                codex_detection_data=detection_data,
                target_base_url="https://api.openai.com",
            )
            
            transformed_body = json.loads(result.content)
            assert transformed_body["messages"][0]["content"] == expected