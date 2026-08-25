from collections.abc import AsyncIterator
from typing import Protocol

from app.model_gateway.providers import ModelGatewayError, ModelProviderAdapter
from app.model_gateway.routing import ModelRouteNotFoundError, ModelRouteRegistry
from app.model_gateway.schemas import ModelRequest, ModelResponse


class ModelGateway(Protocol):
    async def complete(self, request: ModelRequest) -> ModelResponse: ...

    def stream(self, request: ModelRequest) -> AsyncIterator[str]: ...


class UnconfiguredModelGateway:
    """Safe placeholder: no external request is made until a provider adapter is configured."""

    async def complete(self, request: ModelRequest) -> ModelResponse:
        del request
        raise RuntimeError("No model provider adapter is configured")

    async def stream(self, request: ModelRequest) -> AsyncIterator[str]:
        del request
        if False:
            yield ""
        raise RuntimeError("No model provider adapter is configured")


class RoutingModelGateway:
    def __init__(self, routes: ModelRouteRegistry, providers: list[ModelProviderAdapter]) -> None:
        self._routes = routes
        self._providers = {provider.provider: provider for provider in providers}

    async def complete(self, request: ModelRequest) -> ModelResponse:
        try:
            route = self._routes.resolve(request.model)
        except ModelRouteNotFoundError as exc:
            raise ModelGatewayError("MODEL_ROUTE_NOT_FOUND", "逻辑模型未配置或已停用") from exc
        provider = self._providers.get(route.provider)
        if provider is None:
            raise ModelGatewayError(
                "MODEL_PROVIDER_NOT_CONFIGURED",
                "逻辑模型对应的供应商未配置",
                provider=route.provider,
            )
        response = await provider.complete(request, route.provider_model)
        return response.model_copy(update={"route_version": route.version})

    async def stream(self, request: ModelRequest) -> AsyncIterator[str]:
        del request
        if False:
            yield ""
        raise ModelGatewayError("MODEL_STREAM_UNSUPPORTED", "当前模型网关尚未实现跨供应商流式输出")
