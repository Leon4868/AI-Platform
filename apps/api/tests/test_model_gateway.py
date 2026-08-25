from typing import Any

import pytest

from app.container import build_container
from app.core.config import Settings
from app.model_gateway.gateway import RoutingModelGateway, UnconfiguredModelGateway
from app.model_gateway.providers import (
    AnthropicMessagesProvider,
    GeminiGenerateContentProvider,
    ModelGatewayError,
    OpenAIResponsesProvider,
)
from app.model_gateway.routing import ModelRoute, ModelRouteRegistry
from app.model_gateway.schemas import MessageRole, ModelMessage, ModelProvider, ModelRequest
from app.model_gateway.transport import JsonHttpResponse, ModelHttpTransportError


class RecordingTransport:
    def __init__(self, response: JsonHttpResponse | None = None, *, error: str | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> JsonHttpResponse:
        self.calls.append({"url": url, "headers": headers, "payload": payload, "timeout": timeout_seconds})
        if self.error:
            raise ModelHttpTransportError(self.error)
        assert self.response is not None
        return self.response


def request(model: str = "document-standard") -> ModelRequest:
    return ModelRequest(
        model=model,
        messages=[
            ModelMessage(role=MessageRole.SYSTEM, content="只返回正文"),
            ModelMessage(role=MessageRole.USER, content="生成周报"),
        ],
        max_output_tokens=600,
    )


@pytest.mark.asyncio
async def test_openai_responses_adapter_maps_request_response_without_storing() -> None:
    transport = RecordingTransport(JsonHttpResponse(
        200,
        {"x-request-id": "req-header"},
        {
            "id": "resp-1",
            "model": "gpt-enterprise",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "周报正文"}]}],
            "usage": {"input_tokens": 12, "output_tokens": 8},
        },
    ))
    provider = OpenAIResponsesProvider("test-key", transport)

    response = await provider.complete(request(), "gpt-enterprise")

    call = transport.calls[0]
    assert call["url"] == "https://api.openai.com/v1/responses"
    assert call["payload"] == {
        "model": "gpt-enterprise",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "生成周报"}]}],
        "max_output_tokens": 600,
        "temperature": 0.2,
        "store": False,
        "instructions": "只返回正文",
    }
    assert response.model_dump() == {
        "provider": "openai",
        "model": "gpt-enterprise",
        "content": "周报正文",
        "usage": {"input_tokens": 12, "output_tokens": 8},
        "provider_request_id": "resp-1",
        "route_version": None,
    }


@pytest.mark.asyncio
async def test_anthropic_adapter_keeps_system_outside_messages_and_counts_cache_tokens() -> None:
    transport = RecordingTransport(JsonHttpResponse(
        200,
        {},
        {
            "id": "msg-1",
            "model": "claude-enterprise",
            "content": [{"type": "text", "text": "审批稿"}],
            "usage": {
                "input_tokens": 10,
                "cache_creation_input_tokens": 4,
                "cache_read_input_tokens": 3,
                "output_tokens": 5,
            },
        },
    ))
    provider = AnthropicMessagesProvider("test-key", transport)

    response = await provider.complete(request(), "claude-enterprise")

    assert transport.calls[0]["url"] == "https://api.anthropic.com/v1/messages"
    assert transport.calls[0]["headers"]["anthropic-version"] == "2023-06-01"
    assert transport.calls[0]["payload"]["system"] == "只返回正文"
    assert transport.calls[0]["payload"]["messages"] == [{"role": "user", "content": "生成周报"}]
    assert response.usage.input_tokens == 17
    assert response.usage.output_tokens == 5


@pytest.mark.asyncio
async def test_gemini_adapter_uses_header_key_and_generate_content_contract() -> None:
    transport = RecordingTransport(JsonHttpResponse(
        200,
        {},
        {
            "responseId": "gem-1",
            "modelVersion": "gemini-enterprise-001",
            "candidates": [{"content": {"parts": [{"text": "企业稿件"}]}}],
            "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 9},
        },
    ))
    provider = GeminiGenerateContentProvider("test-key", transport)

    response = await provider.complete(request(), "gemini enterprise")

    call = transport.calls[0]
    assert call["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/"
        "models/gemini%20enterprise:generateContent"
    )
    assert call["headers"]["x-goog-api-key"] == "test-key"
    assert call["payload"]["systemInstruction"] == {"parts": [{"text": "只返回正文"}]}
    assert response.content == "企业稿件"
    assert response.provider_request_id == "gem-1"


@pytest.mark.asyncio
async def test_routing_is_explicit_versioned_and_has_no_fallback() -> None:
    transport = RecordingTransport(JsonHttpResponse(
        200,
        {},
        {
            "id": "resp-1",
            "model": "gpt-enterprise",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "正文"}]}],
            "usage": {},
        },
    ))
    gateway = RoutingModelGateway(
        ModelRouteRegistry([ModelRoute(
            logical_model_code="document-standard",
            version=7,
            provider=ModelProvider.OPENAI,
            provider_model="gpt-enterprise",
        )]),
        [OpenAIResponsesProvider("test-key", transport)],
    )

    response = await gateway.complete(request())
    assert response.route_version == 7
    assert response.provider is ModelProvider.OPENAI

    with pytest.raises(ModelGatewayError) as missing:
        await gateway.complete(request("unknown-model"))
    assert missing.value.code == "MODEL_ROUTE_NOT_FOUND"
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_provider_errors_are_sanitized_and_retryability_is_bounded() -> None:
    rate_limited = RecordingTransport(JsonHttpResponse(429, {}, {"secret": "must-not-leak"}))
    provider = OpenAIResponsesProvider("test-key", rate_limited)

    with pytest.raises(ModelGatewayError) as error:
        await provider.complete(request(), "gpt-enterprise")
    assert error.value.code == "MODEL_RATE_LIMITED"
    assert error.value.retryable is True
    assert "must-not-leak" not in str(error.value)

    timeout = OpenAIResponsesProvider("test-key", RecordingTransport(error="timeout"))
    with pytest.raises(ModelGatewayError) as timeout_error:
        await timeout.complete(request(), "gpt-enterprise")
    assert timeout_error.value.code == "MODEL_TIMEOUT"
    assert timeout_error.value.retryable is True


@pytest.mark.asyncio
async def test_unsupported_tool_and_streaming_fail_before_network() -> None:
    transport = RecordingTransport(JsonHttpResponse(200, {}, {}))
    provider = AnthropicMessagesProvider("test-key", transport)
    tool_request = ModelRequest(
        model="document-standard",
        messages=[ModelMessage(role=MessageRole.TOOL, content="tool result")],
    )

    with pytest.raises(ModelGatewayError) as unsupported:
        await provider.complete(tool_request, "claude-enterprise")
    assert unsupported.value.code == "MODEL_REQUEST_UNSUPPORTED"
    assert transport.calls == []

    gateway = RoutingModelGateway(ModelRouteRegistry([]), [])
    with pytest.raises(ModelGatewayError) as stream_error:
        await anext(gateway.stream(request()))
    assert stream_error.value.code == "MODEL_STREAM_UNSUPPORTED"


@pytest.mark.asyncio
async def test_unconfigured_gateway_makes_no_external_call() -> None:
    gateway = UnconfiguredModelGateway()
    with pytest.raises(RuntimeError, match="No model provider"):
        await gateway.complete(request())


def test_container_requires_explicit_route_for_external_document_composer() -> None:
    with pytest.raises(RuntimeError, match="requires at least one enabled model route"):
        build_container(Settings(environment="test", document_composer="model_gateway"))


@pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini"])
@pytest.mark.asyncio
async def test_configured_route_without_key_fails_safely_without_network(provider: str) -> None:
    container = build_container(Settings(
        environment="test",
        model_routes_json=(
            '{"routes":[{"logicalModelCode":"document-standard","version":1,'
            f'"provider":"{provider}","providerModel":"enterprise-model","enabled":true}}]}}'
        ),
    ))

    with pytest.raises(ModelGatewayError) as error:
        await container.model_gateway.complete(request())
    assert error.value.code == "MODEL_PROVIDER_NOT_CONFIGURED"
