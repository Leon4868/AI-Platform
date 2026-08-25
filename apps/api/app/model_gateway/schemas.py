from enum import StrEnum
from typing import Any

from pydantic import Field

from app.core.schemas import ApiModel


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ModelProvider(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"


class ModelMessage(ApiModel):
    role: MessageRole
    content: str = Field(min_length=1, max_length=200_000)


class ModelRequest(ApiModel):
    model: str = Field(min_length=1, max_length=120)
    messages: list[ModelMessage] = Field(min_length=1, max_length=200)
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_output_tokens: int = Field(default=2_000, ge=1, le=100_000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelUsage(ApiModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class ModelResponse(ApiModel):
    provider: ModelProvider
    model: str
    content: str
    usage: ModelUsage
    provider_request_id: str | None = None
    route_version: int | None = Field(default=None, ge=1)
