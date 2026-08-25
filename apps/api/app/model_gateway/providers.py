from abc import ABC, abstractmethod
from typing import Any, Protocol
from urllib.parse import quote

from app.model_gateway.schemas import (
    MessageRole,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelUsage,
)
from app.model_gateway.transport import ModelHttpTransport, ModelHttpTransportError


class ModelGatewayError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        provider: ModelProvider | None = None,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.provider = provider
        self.retryable = retryable
        self.status_code = status_code


class ModelProviderAdapter(Protocol):
    provider: ModelProvider

    async def complete(self, request: ModelRequest, provider_model: str) -> ModelResponse: ...


class BaseJsonProvider(ABC):
    provider: ModelProvider

    def __init__(self, api_key: str, transport: ModelHttpTransport, *, timeout_seconds: float = 60) -> None:
        if not api_key:
            raise ValueError("provider API key must be configured")
        self._api_key = api_key
        self._transport = transport
        self._timeout_seconds = timeout_seconds

    async def complete(self, request: ModelRequest, provider_model: str) -> ModelResponse:
        try:
            response = await self._transport.post_json(
                self.url(provider_model),
                headers=self.headers(),
                payload=self.payload(request, provider_model),
                timeout_seconds=self._timeout_seconds,
            )
        except ModelHttpTransportError as exc:
            mapping = {
                "timeout": ("MODEL_TIMEOUT", "模型供应商请求超时"),
                "network": ("MODEL_UNAVAILABLE", "模型供应商网络不可用"),
                "invalid_json": ("MODEL_INVALID_RESPONSE", "模型供应商响应格式无效"),
            }
            code, message = mapping.get(exc.kind, ("MODEL_UNAVAILABLE", "模型供应商请求失败"))
            raise ModelGatewayError(code, message, provider=self.provider, retryable=exc.kind != "invalid_json") from exc

        if response.status_code < 200 or response.status_code >= 300:
            retryable = response.status_code == 429 or response.status_code >= 500
            raise ModelGatewayError(
                "MODEL_RATE_LIMITED" if response.status_code == 429 else "MODEL_UNAVAILABLE",
                "模型供应商暂时不可用" if retryable else "模型供应商拒绝了请求",
                provider=self.provider,
                retryable=retryable,
                status_code=response.status_code,
            )
        try:
            return self.parse(response.payload, provider_model, response.headers)
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise ModelGatewayError(
                "MODEL_INVALID_RESPONSE",
                "模型供应商响应缺少必要字段",
                provider=self.provider,
            ) from exc

    @abstractmethod
    def url(self, provider_model: str) -> str: ...

    @abstractmethod
    def headers(self) -> dict[str, str]: ...

    @abstractmethod
    def payload(self, request: ModelRequest, provider_model: str) -> dict[str, Any]: ...

    @abstractmethod
    def parse(self, payload: Any, provider_model: str, headers: dict[str, str]) -> ModelResponse: ...

    def _require_text(self, parts: list[str]) -> str:
        content = "".join(parts).strip()
        if not content:
            raise ValueError("empty model response")
        return content


def _system_text(request: ModelRequest) -> str | None:
    parts = [message.content for message in request.messages if message.role is MessageRole.SYSTEM]
    return "\n\n".join(parts) or None


def _reject_tool_messages(request: ModelRequest) -> None:
    if any(message.role is MessageRole.TOOL for message in request.messages):
        raise ModelGatewayError("MODEL_REQUEST_UNSUPPORTED", "当前文本适配器不接受 tool 消息")


class OpenAIResponsesProvider(BaseJsonProvider):
    provider = ModelProvider.OPENAI

    def __init__(self, api_key: str, transport: ModelHttpTransport, *, base_url: str = "https://api.openai.com/v1", timeout_seconds: float = 60) -> None:
        super().__init__(api_key, transport, timeout_seconds=timeout_seconds)
        self._base_url = base_url.rstrip("/")

    def url(self, provider_model: str) -> str:
        del provider_model
        return f"{self._base_url}/responses"

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

    def payload(self, request: ModelRequest, provider_model: str) -> dict[str, Any]:
        _reject_tool_messages(request)
        messages = [
            {"role": message.role.value, "content": [{"type": "input_text", "text": message.content}]}
            for message in request.messages
            if message.role is not MessageRole.SYSTEM
        ]
        body: dict[str, Any] = {
            "model": provider_model,
            "input": messages,
            "max_output_tokens": request.max_output_tokens,
            "temperature": request.temperature,
            "store": False,
        }
        if instructions := _system_text(request):
            body["instructions"] = instructions
        return body

    def parse(self, payload: Any, provider_model: str, headers: dict[str, str]) -> ModelResponse:
        text_parts: list[str] = []
        for item in payload["output"]:
            if item.get("type") != "message":
                continue
            for part in item.get("content", []):
                if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    text_parts.append(part["text"])
        usage = payload.get("usage") or {}
        return ModelResponse(
            provider=self.provider,
            model=payload.get("model") or provider_model,
            content=self._require_text(text_parts),
            usage=ModelUsage(
                input_tokens=int(usage.get("input_tokens", 0)),
                output_tokens=int(usage.get("output_tokens", 0)),
            ),
            provider_request_id=payload.get("id") or headers.get("x-request-id"),
        )


class AnthropicMessagesProvider(BaseJsonProvider):
    provider = ModelProvider.ANTHROPIC

    def __init__(self, api_key: str, transport: ModelHttpTransport, *, base_url: str = "https://api.anthropic.com", timeout_seconds: float = 60) -> None:
        super().__init__(api_key, transport, timeout_seconds=timeout_seconds)
        self._base_url = base_url.rstrip("/")

    def url(self, provider_model: str) -> str:
        del provider_model
        return f"{self._base_url}/v1/messages"

    def headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    def payload(self, request: ModelRequest, provider_model: str) -> dict[str, Any]:
        _reject_tool_messages(request)
        body: dict[str, Any] = {
            "model": provider_model,
            "messages": [
                {"role": message.role.value, "content": message.content}
                for message in request.messages
                if message.role is not MessageRole.SYSTEM
            ],
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
        }
        if system := _system_text(request):
            body["system"] = system
        return body

    def parse(self, payload: Any, provider_model: str, headers: dict[str, str]) -> ModelResponse:
        parts = [part["text"] for part in payload["content"] if part.get("type") == "text" and isinstance(part.get("text"), str)]
        usage = payload.get("usage") or {}
        input_tokens = sum(int(usage.get(key, 0)) for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        return ModelResponse(
            provider=self.provider,
            model=payload.get("model") or provider_model,
            content=self._require_text(parts),
            usage=ModelUsage(input_tokens=input_tokens, output_tokens=int(usage.get("output_tokens", 0))),
            provider_request_id=payload.get("id") or headers.get("request-id"),
        )


class GeminiGenerateContentProvider(BaseJsonProvider):
    provider = ModelProvider.GEMINI

    def __init__(self, api_key: str, transport: ModelHttpTransport, *, base_url: str = "https://generativelanguage.googleapis.com/v1beta", timeout_seconds: float = 60) -> None:
        super().__init__(api_key, transport, timeout_seconds=timeout_seconds)
        self._base_url = base_url.rstrip("/")

    def url(self, provider_model: str) -> str:
        return f"{self._base_url}/models/{quote(provider_model, safe='')}:generateContent"

    def headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self._api_key, "Content-Type": "application/json"}

    def payload(self, request: ModelRequest, provider_model: str) -> dict[str, Any]:
        del provider_model
        _reject_tool_messages(request)
        body: dict[str, Any] = {
            "contents": [
                {
                    "role": "model" if message.role is MessageRole.ASSISTANT else "user",
                    "parts": [{"text": message.content}],
                }
                for message in request.messages
                if message.role is not MessageRole.SYSTEM
            ],
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_output_tokens,
            },
        }
        if system := _system_text(request):
            body["systemInstruction"] = {"parts": [{"text": system}]}
        return body

    def parse(self, payload: Any, provider_model: str, headers: dict[str, str]) -> ModelResponse:
        parts: list[str] = []
        for candidate in payload["candidates"]:
            for part in candidate.get("content", {}).get("parts", []):
                if isinstance(part.get("text"), str):
                    parts.append(part["text"])
        usage = payload.get("usageMetadata") or {}
        return ModelResponse(
            provider=self.provider,
            model=payload.get("modelVersion") or provider_model,
            content=self._require_text(parts),
            usage=ModelUsage(
                input_tokens=int(usage.get("promptTokenCount", 0)),
                output_tokens=int(usage.get("candidatesTokenCount", 0)),
            ),
            provider_request_id=payload.get("responseId") or headers.get("x-request-id"),
        )
