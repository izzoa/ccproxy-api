import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from ccproxy.api.middleware.model_validation import (
    ModelValidationError,
    ModelValidationMiddleware,
)
from ccproxy.models.provider import ModelCard
from ccproxy.utils.model_registry import ModelRegistry


@pytest.fixture
def sample_model_card():
    return ModelCard(
        id="claude-3-5-sonnet-20241022",
        object="model",
        owned_by="anthropic",
        max_input_tokens=200000,
        max_output_tokens=8192,
        supports_vision=True,
        supports_function_calling=True,
        supports_response_schema=True,
    )


@pytest.fixture
def mock_registry(sample_model_card):
    registry = MagicMock(spec=ModelRegistry)
    registry.get_model = AsyncMock(return_value=sample_model_card)
    return registry


@pytest.fixture
def app_with_validation(mock_registry):
    app = FastAPI()

    middleware = ModelValidationMiddleware(
        app=app,
        validate_token_limits=True,
        enforce_capabilities=True,
        warn_on_limits=True,
    )
    middleware.registry = mock_registry

    app.add_middleware(ModelValidationMiddleware, validate_token_limits=True)

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        body = await request.json()
        return {"model": body.get("model"), "choices": []}

    return app


def test_should_validate_chat_completions():
    app = FastAPI()
    middleware = ModelValidationMiddleware(app=app)

    request = MagicMock(spec=Request)
    request.url.path = "/v1/chat/completions"

    assert middleware._should_validate(request) is True


def test_should_not_validate_other_paths():
    app = FastAPI()
    middleware = ModelValidationMiddleware(app=app)

    request = MagicMock(spec=Request)
    request.url.path = "/v1/models"

    assert middleware._should_validate(request) is False


def test_infer_provider_anthropic():
    app = FastAPI()
    middleware = ModelValidationMiddleware(app=app)

    provider = middleware._infer_provider("/v1/messages")
    assert provider == "anthropic"


def test_infer_provider_openai():
    app = FastAPI()
    middleware = ModelValidationMiddleware(app=app)

    provider = middleware._infer_provider("/v1/chat/completions")
    assert provider is None


def test_has_vision_content():
    app = FastAPI()
    middleware = ModelValidationMiddleware(app=app)

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What's in this image?"},
                {"type": "image_url", "image_url": {"url": "https://example.com/img.jpg"}},
            ],
        }
    ]

    assert middleware._has_vision_content(messages) is True


def test_no_vision_content():
    app = FastAPI()
    middleware = ModelValidationMiddleware(app=app)

    messages = [{"role": "user", "content": "Hello"}]

    assert middleware._has_vision_content(messages) is False


def test_validate_token_limits_exceeded(sample_model_card):
    app = FastAPI()
    middleware = ModelValidationMiddleware(app=app)

    request_data = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": "Hello"}],
    }

    input_tokens = 250000

    error = middleware._validate_token_limits(request_data, sample_model_card, input_tokens)

    assert error is not None
    assert "context_length_exceeded" in error["code"]


def test_validate_token_limits_ok(sample_model_card):
    app = FastAPI()
    middleware = ModelValidationMiddleware(app=app)

    request_data = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": "Hello"}],
    }

    input_tokens = 1000

    error = middleware._validate_token_limits(request_data, sample_model_card, input_tokens)

    assert error is None


def test_validate_output_tokens_exceeded(sample_model_card):
    app = FastAPI()
    middleware = ModelValidationMiddleware(app=app)

    request_data = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": "Hello"}],
        "max_tokens": 10000,
    }

    input_tokens = 1000

    error = middleware._validate_token_limits(request_data, sample_model_card, input_tokens)

    assert error is not None
    assert "max_tokens_exceeded" in error["code"]


def test_check_token_warnings(sample_model_card):
    app = FastAPI()
    middleware = ModelValidationMiddleware(app=app, warn_threshold=0.9)

    request_data = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": "Hello"}],
    }

    input_tokens = 185000

    warnings = middleware._check_token_warnings(request_data, sample_model_card, input_tokens)

    assert len(warnings) > 0
    assert "92.5%" in warnings[0]


def test_validate_capabilities_vision_unsupported():
    app = FastAPI()
    middleware = ModelValidationMiddleware(app=app)

    model_card = ModelCard(
        id="gpt-3.5-turbo",
        object="model",
        owned_by="openai",
        supports_vision=False,
    )

    request_data = {
        "model": "gpt-3.5-turbo",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What's this?"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/img.jpg"}},
                ],
            }
        ],
    }

    error = middleware._validate_capabilities(request_data, model_card)

    assert error is not None
    assert "unsupported_content_type" in error["code"]


def test_validate_capabilities_function_calling_unsupported():
    app = FastAPI()
    middleware = ModelValidationMiddleware(app=app)

    model_card = ModelCard(
        id="claude-instant-1.2",
        object="model",
        owned_by="anthropic",
        supports_function_calling=False,
    )

    request_data = {
        "model": "claude-instant-1.2",
        "messages": [{"role": "user", "content": "Get weather"}],
        "tools": [{"type": "function", "function": {"name": "get_weather"}}],
    }

    error = middleware._validate_capabilities(request_data, model_card)

    assert error is not None
    assert "unsupported_feature" in error["code"]


def test_validate_capabilities_response_schema_unsupported():
    app = FastAPI()
    middleware = ModelValidationMiddleware(app=app)

    model_card = ModelCard(
        id="gpt-3.5-turbo",
        object="model",
        owned_by="openai",
        supports_response_schema=False,
    )

    request_data = {
        "model": "gpt-3.5-turbo",
        "messages": [{"role": "user", "content": "Hello"}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "test"}},
    }

    error = middleware._validate_capabilities(request_data, model_card)

    assert error is not None
    assert "unsupported_feature" in error["code"]


def test_create_error_response():
    app = FastAPI()
    middleware = ModelValidationMiddleware(app=app)

    error = {
        "message": "Token limit exceeded",
        "type": "invalid_request_error",
        "param": "messages",
        "code": "context_length_exceeded",
    }

    response = middleware._create_error_response(error)

    assert response.status_code == 400
    body = json.loads(response.body)
    assert body["error"]["message"] == "Token limit exceeded"
    assert body["error"]["code"] == "context_length_exceeded"


@pytest.mark.asyncio
async def test_dispatch_skips_non_validated_paths(mock_registry):
    app = FastAPI()
    middleware = ModelValidationMiddleware(app=app)
    middleware.registry = mock_registry

    request = MagicMock(spec=Request)
    request.url.path = "/v1/models"

    call_next = AsyncMock(return_value=Response(content="OK"))

    response = await middleware.dispatch(request, call_next)

    assert response.body == b"OK"
    call_next.assert_called_once()


@pytest.mark.asyncio
async def test_dispatch_validates_and_passes_through(mock_registry, sample_model_card):
    app = FastAPI()
    middleware = ModelValidationMiddleware(
        app=app, validate_token_limits=False, enforce_capabilities=False
    )
    middleware.registry = mock_registry

    request_body = json.dumps(
        {"model": "claude-3-5-sonnet-20241022", "messages": [{"role": "user", "content": "Hi"}]}
    ).encode()

    request = MagicMock(spec=Request)
    request.url.path = "/v1/messages"
    request.body = AsyncMock(return_value=request_body)
    request.state = MagicMock()

    call_next = AsyncMock(return_value=Response(content="OK"))

    response = await middleware.dispatch(request, call_next)

    assert response.body == b"OK"
    mock_registry.get_model.assert_called_once()