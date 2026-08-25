from collections.abc import Iterable

from pydantic import Field, model_validator

from app.core.schemas import ContractModel
from app.model_gateway.schemas import ModelProvider


class ModelRoute(ContractModel):
    logical_model_code: str = Field(min_length=1, max_length=120)
    version: int = Field(ge=1)
    provider: ModelProvider
    provider_model: str = Field(min_length=1, max_length=160)
    enabled: bool = True


class ModelRoutingConfig(ContractModel):
    routes: list[ModelRoute] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_logical_codes(self) -> "ModelRoutingConfig":
        codes = [route.logical_model_code for route in self.routes]
        if len(codes) != len(set(codes)):
            raise ValueError("logical model codes must be unique")
        return self


class ModelRouteNotFoundError(LookupError):
    pass


class ModelRouteRegistry:
    """Immutable active routing snapshot; never performs provider fallback."""

    def __init__(self, routes: Iterable[ModelRoute]) -> None:
        config = ModelRoutingConfig(routes=list(routes))
        self._routes = {route.logical_model_code: route for route in config.routes}

    def resolve(self, logical_model_code: str) -> ModelRoute:
        route = self._routes.get(logical_model_code)
        if route is None or not route.enabled:
            raise ModelRouteNotFoundError(logical_model_code)
        return route

    def snapshot(self) -> tuple[ModelRoute, ...]:
        return tuple(self._routes[code].model_copy(deep=True) for code in sorted(self._routes))
