"""Unit tests for the Codex ResponseAdapter conversions."""

import pytest

from ccproxy.adapters.openai.response_adapter import (
    ResponseAdapter,
    UnsupportedCodexModelError,
    UnsupportedOpenAIParametersError,
)
from ccproxy.config.codex import CodexSettings


def _basic_messages() -> list[dict[str, str]]:
    return [{"role": "user", "content": "Hello"}]


class StubModelInfoService:
    """Simple stub for ModelInfoService used in tests."""

    def __init__(self, max_output_tokens: int = 4096, available: list[str] | None = None) -> None:
        self.max_output_tokens = max_output_tokens
        self.available = available or ["gpt-4o", "gpt-5"]
        self.requested_models: list[str] = []

    async def get_max_output_tokens(self, model_name: str) -> int:
        self.requested_models.append(model_name)
        return self.max_output_tokens

    async def get_available_models(self) -> list[str]:
        return self.available


def test_parameter_translation_includes_supported_fields() -> None:
    settings = CodexSettings()
    adapter = ResponseAdapter(codex_settings=settings)

    request = adapter.chat_to_response_request(
        {
            "model": "gpt-4o",
            "messages": _basic_messages(),
            "temperature": 0.5,
            "top_p": 0.9,
            "max_tokens": 256,
            "frequency_penalty": 0.1,
            "presence_penalty": 0.2,
            "logit_bias": {42: -5},
            "seed": 123,
            "stop": ["###"],
            "store": True,
            "metadata": {"custom": "value"},
            "user": "tester",
        }
    )

    assert request.model == "gpt-4o"
    assert request.temperature == 0.5
    assert request.top_p == 0.9
    assert request.max_output_tokens == 256
    assert request.frequency_penalty == 0.1
    assert request.presence_penalty == 0.2
    assert request.logit_bias == {"42": -5}
    assert request.seed == 123
    assert request.stop == ["###"]
    assert request.store is True
    assert request.metadata == {"custom": "value", "user": "tester"}


def test_unsupported_parameter_raises_error() -> None:
    adapter = ResponseAdapter(codex_settings=CodexSettings())

    with pytest.raises(UnsupportedOpenAIParametersError):
        adapter.chat_to_response_request(
            {
                "model": "gpt-4o",
                "messages": _basic_messages(),
                "n": 2,
            }
        )


def test_unknown_model_raises_error() -> None:
    adapter = ResponseAdapter(codex_settings=CodexSettings())

    with pytest.raises(UnsupportedCodexModelError):
        adapter.chat_to_response_request(
            {
                "model": "made-up-model",
                "messages": _basic_messages(),
            }
        )


@pytest.mark.asyncio
async def test_dynamic_model_info_applies_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_service = StubModelInfoService(max_output_tokens=2048, available=["gpt-4o", "gpt-5"])
    monkeypatch.setattr(
        "ccproxy.adapters.openai.response_adapter.get_model_info_service",
        lambda: stub_service,
    )

    settings = CodexSettings(enable_dynamic_model_info=True)
    adapter = ResponseAdapter(codex_settings=settings)

    request = await adapter.chat_to_response_request_async(
        {
            "model": "gpt-5",
            "messages": _basic_messages(),
        }
    )

    assert request.model == "gpt-5"
    assert request.max_output_tokens == 2048
    assert stub_service.requested_models == ["gpt-5"]


def test_dynamic_model_info_disabled_uses_fallback() -> None:
    settings = CodexSettings(enable_dynamic_model_info=False, max_output_tokens_fallback=1024)
    adapter = ResponseAdapter(codex_settings=settings)

    request = adapter.chat_to_response_request(
        {
            "model": "gpt-4o",
            "messages": _basic_messages(),
        }
    )

    assert request.max_output_tokens == 1024


def test_propagate_unsupported_params_allows_request() -> None:
    settings = CodexSettings(propagate_unsupported_params=True)
    adapter = ResponseAdapter(codex_settings=settings)

    request = adapter.chat_to_response_request(
        {
            "model": "gpt-4o",
            "messages": _basic_messages(),
            "n": 3,
        }
    )

    assert request.model == "gpt-4o"
    assert request.max_output_tokens is None


def test_model_aliases_map_to_supported_model() -> None:
    adapter = ResponseAdapter(codex_settings=CodexSettings())

    request = adapter.chat_to_response_request(
        {
            "model": "gpt-4.1",
            "messages": _basic_messages(),
        }
    )

    assert request.model == "gpt-4o"
