from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Secret values are injected, never committed."""

    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_file=None,
        extra="ignore",
    )

    app_name: str = "Enterprise AI Platform API"
    environment: Literal["development", "test", "production"] = "development"
    api_prefix: str = "/api/v1"
    database_url: str | None = Field(default=None, repr=False)
    repository_backend: Literal["memory", "postgresql"] = "memory"
    storage_backend: Literal["memory", "s3"] = "memory"
    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_bucket: str = "enterprise-ai-platform"
    s3_access_key: str | None = Field(default=None, repr=False)
    s3_secret_key: str | None = Field(default=None, repr=False)
    s3_use_path_style: bool = False
    embedding_dimensions: int = Field(default=1536, ge=128, le=4096)
    document_composer: Literal["deterministic", "model_gateway"] = "deterministic"
    model_routes_json: str = '{"routes":[]}'
    model_request_timeout_seconds: float = Field(default=60, ge=1, le=600)
    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY", repr=False)
    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY", repr=False)
    gemini_api_key: SecretStr | None = Field(default=None, validation_alias="GEMINI_API_KEY", repr=False)
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_base_url: str = "https://api.anthropic.com"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    cors_allowed_origins: str = ""

    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
