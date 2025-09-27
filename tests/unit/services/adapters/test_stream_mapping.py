from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic import ValidationError

from ccproxy.llms.models.openai import ChatCompletionChunk
from ccproxy.services.adapters.simple_converters import map_stream


async def _aiter(items: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
    for it in items:
        yield it


class DummyConverter:
    def __init__(self) -> None:
        self.calls: int = 0

    def __call__(self, stream: AsyncIterator[Any]) -> AsyncIterator[Any]:  # type: ignore[override]
        async def _gen() -> AsyncIterator[Any]:
            async for evt in stream:
                # echo back a minimal object with model_dump method
                self.calls += 1

                # Build a plain dict from evt
                if isinstance(evt, dict):
                    data = evt
                elif hasattr(evt, "model_dump"):
                    data = evt.model_dump(exclude_unset=True)  # type: ignore[attr-defined]
                else:
                    data = dict(getattr(evt, "__dict__", {}))

                class Obj:
                    def __init__(self, d: dict[str, Any]) -> None:
                        self._d = d

                    def model_dump(
                        self, *, exclude_unset: bool = True
                    ) -> dict[str, Any]:
                        return self._d

                yield Obj(data)

        return _gen()


@pytest.mark.asyncio
async def test_map_stream_validates_and_maps() -> None:
    # Provide minimal-but-valid chunks so the validator succeeds without fallbacks
    chunks: list[dict[str, Any]] = [
        {
            "id": "c1",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "gpt-4o-mini",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": "hello"},
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "c2",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "gpt-4o-mini",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": "world"},
                    "finish_reason": None,
                }
            ],
        },
    ]

    dummy = DummyConverter()

    out: list[dict[str, Any]] = []
    async for item in map_stream(
        _aiter(chunks), validator_model=ChatCompletionChunk, converter=dummy
    ):
        out.append(item)

    assert len(out) == 2
    assert out[0]["id"] == "c1"
    assert out[1]["id"] == "c2"
    # Converter sees two events
    assert dummy.calls == 2


@pytest.mark.asyncio
async def test_map_stream_raises_on_invalid_data() -> None:
    # Invalid payloads now bubble up as validation errors without dict fallbacks
    chunks = [
        {"unexpected": True},
    ]
    dummy = DummyConverter()

    with pytest.raises(ValidationError):
        async for _ in map_stream(
            _aiter(chunks), validator_model=ChatCompletionChunk, converter=dummy
        ):
            pass

    assert dummy.calls == 0
