from typing import Any

import pytest

from ccproxy.llms.formatters.openai_to_openai import (
    convert__openai_chat_to_openai_responses__request,
    convert__openai_chat_to_openai_responses__response,
    convert__openai_responses_to_openai_chat__response,
    convert__openai_responses_to_openai_chat__stream,
    convert__openai_responses_to_openaichat__request,
)
from ccproxy.llms.models import openai as openai_models


def _make_response_object_with_reasoning() -> openai_models.ResponseObject:
    reasoning_summary = [
        {"type": "summary_text", "text": "Thoughts", "signature": "sig"}
    ]
    return openai_models.ResponseObject(
        id="resp-1",
        object="response",
        created_at=0,
        status="completed",
        model="gpt-test",
        output=[
            openai_models.ReasoningOutput(
                type="reasoning",
                id="reasoning-1",
                status="completed",
                summary=reasoning_summary,
            ),
            openai_models.MessageOutput(
                type="message",
                role="assistant",
                id="msg-1",
                status="completed",
                content=[
                    openai_models.OutputTextContent(
                        type="output_text", text="Final answer"
                    )
                ],
            ),
        ],
        parallel_tool_calls=False,
    )


def test_responses_to_chat_serializes_thinking() -> None:
    response = _make_response_object_with_reasoning()

    chat = convert__openai_responses_to_openai_chat__response(response)

    choice = chat.choices[0]
    assert choice.message.content == (
        '<thinking signature="sig">Thoughts</thinking>Final answer'
    )


@pytest.mark.asyncio
async def test_chat_to_responses_extracts_thinking() -> None:
    chat_response = openai_models.ChatCompletionResponse(
        id="chat-1",
        created=0,
        model="gpt-test",
        object="chat.completion",
        choices=[
            openai_models.Choice(
                index=0,
                message=openai_models.ResponseMessage(
                    role="assistant",
                    content='<thinking signature="sig">Thoughts</thinking>Final answer',
                ),
                finish_reason="stop",
            )
        ],
    )

    response = await convert__openai_chat_to_openai_responses__response(chat_response)

    reasoning_items = [
        entry for entry in response.output if _get_type(entry) == "reasoning"
    ]
    assert len(reasoning_items) == 1
    summary = reasoning_items[0].summary  # type: ignore[attr-defined]
    assert summary is not None
    assert summary[0]["text"] == "Thoughts"
    assert summary[0]["signature"] == "sig"

    message_items = [
        entry for entry in response.output if _get_type(entry) == "message"
    ]
    assert len(message_items) == 1
    content = message_items[0].content[0].text  # type: ignore[attr-defined]
    assert content == "Final answer"

    assert response.reasoning is not None
    assert response.reasoning.summary  # type: ignore[union-attr]


def _get_type(entry: object) -> str | None:
    return getattr(entry, "type", None)


def test_responses_to_chat_handles_nested_summary_blocks() -> None:
    nested_summary = [
        {
            "type": "summary_group",
            "content": [
                {"type": "summary_text", "text": "First part. "},
                {"type": "summary_text", "text": "Second part."},
                {"type": "signature", "text": "sig-nested"},
            ],
        }
    ]

    response = openai_models.ResponseObject(
        id="resp-nested",
        object="response",
        created_at=0,
        status="completed",
        model="gpt-test",
        output=[
            openai_models.ReasoningOutput(
                type="reasoning",
                id="reasoning-nested",
                status="completed",
                summary=nested_summary,
            ),
        ],
        parallel_tool_calls=False,
    )

    chat = convert__openai_responses_to_openai_chat__response(response)
    choice = chat.choices[0]

    assert (
        choice.message.content
        == '<thinking signature="sig-nested">First part. Second part.</thinking>'
    )


def test_responses_to_chat_ignores_summary_mode_strings() -> None:
    response = openai_models.ResponseObject(
        id="resp-summary-mode",
        object="response",
        created_at=0,
        status="completed",
        model="gpt-test",
        output=[],
        parallel_tool_calls=False,
        reasoning=openai_models.Reasoning(summary="detailed"),
    )

    chat = convert__openai_responses_to_openai_chat__response(response)

    choice = chat.choices[0]
    assert choice.message.content == ""


@pytest.mark.asyncio
async def test_responses_request_to_chat_maps_reasoning_effort() -> None:
    request = openai_models.ResponseRequest(
        model="gpt-test",
        input="Hello",
        reasoning={"effort": "medium"},
    )

    chat_request = await convert__openai_responses_to_openaichat__request(request)

    assert chat_request.reasoning_effort == "medium"


@pytest.mark.asyncio
async def test_chat_request_to_responses_maps_reasoning_effort() -> None:
    chat_request = openai_models.ChatCompletionRequest(
        model="gpt-test",
        messages=[openai_models.ChatMessage(role="user", content="Hello")],
        reasoning_effort="high",
    )

    response_request = await convert__openai_chat_to_openai_responses__request(
        chat_request
    )

    assert response_request.reasoning == {"effort": "high", "summary": "auto"}


@pytest.mark.asyncio
async def test_chat_request_to_responses_defaults_reasoning(monkeypatch: Any) -> None:
    monkeypatch.delenv("LLM__OPENAI_THINKING_XML", raising=False)
    monkeypatch.delenv("OPENAI_STREAM_ENABLE_THINKING_SERIALIZATION", raising=False)

    chat_request = openai_models.ChatCompletionRequest(
        model="gpt-test",
        messages=[openai_models.ChatMessage(role="user", content="Hello")],
    )

    response_request = await convert__openai_chat_to_openai_responses__request(
        chat_request
    )

    assert response_request.reasoning == {"effort": "medium", "summary": "auto"}


@pytest.mark.asyncio
async def test_chat_request_to_responses_respects_disable_env(monkeypatch: Any) -> None:
    monkeypatch.setenv("LLM__OPENAI_THINKING_XML", "false")

    chat_request = openai_models.ChatCompletionRequest(
        model="gpt-test",
        messages=[openai_models.ChatMessage(role="user", content="Hello")],
    )

    response_request = await convert__openai_chat_to_openai_responses__request(
        chat_request
    )

    assert response_request.reasoning is None


@pytest.mark.asyncio
async def test_responses_stream_includes_thinking_xml() -> None:
    async def source():
        yield openai_models.ResponseCreatedEvent(
            type="response.created",
            sequence_number=1,
            response=_make_response_object_with_reasoning(),
        )
        yield openai_models.ReasoningSummaryPartAddedEvent(
            type="response.reasoning_summary_part.added",
            sequence_number=2,
            item_id="reasoning-1",
            output_index=0,
            summary_index=0,
            part=openai_models.ReasoningSummaryPart(type="signature", text="sig"),
        )
        yield openai_models.ReasoningSummaryTextDeltaEvent(
            type="response.reasoning_summary_text.delta",
            sequence_number=3,
            item_id="reasoning-1",
            output_index=0,
            summary_index=0,
            delta="Thoughts",
        )
        yield openai_models.ReasoningSummaryTextDoneEvent(
            type="response.reasoning_summary_text.done",
            sequence_number=4,
            item_id="reasoning-1",
            output_index=0,
            summary_index=0,
            text="Thoughts",
        )
        yield openai_models.ResponseOutputTextDeltaEvent(
            type="response.output_text.delta",
            sequence_number=5,
            item_id="msg_stream",
            output_index=0,
            content_index=0,
            delta="Final answer",
        )

    deltas: list[str] = []

    async for chunk in convert__openai_responses_to_openai_chat__stream(source()):
        if chunk.choices:
            delta_msg = chunk.choices[0].delta
            if delta_msg and delta_msg.content:
                deltas.append(delta_msg.content)

    assert deltas[0] == '<thinking signature="sig">Thoughts'
    assert deltas[1] == "</thinking>"
    assert deltas[2] == "Final answer"
