from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Existing GitHub PAT settings (for backward compatibility)
    github_token: str = Field("", alias="GITHUB_TOKEN")
    github_webhook_secret: str = Field("", alias="GITHUB_WEBHOOK_SECRET")

    # NEW: GitHub App settings
    github_app_id: str = Field("", alias="GITHUB_APP_ID")
    github_app_client_id: str = Field("", alias="GITHUB_APP_CLIENT_ID")
    github_app_client_secret: str = Field("", alias="GITHUB_APP_CLIENT_SECRET")
    github_app_webhook_secret: str = Field("", alias="GITHUB_APP_WEBHOOK_SECRET")
    github_app_private_key: str = Field("", alias="GITHUB_APP_PRIVATE_KEY")

    # NEW: Database settings
    database_url: str = Field("postgresql://user:pass@localhost/prguardian", alias="DATABASE_URL")

    # NEW: Application settings
    app_name: str = Field("PR Guardian AI", alias="APP_NAME")
    app_url: str = Field("http://localhost:8000", alias="APP_URL")

    # NEW: Security
    secret_key: str = Field("change-me-in-production", alias="SECRET_KEY")
    jwt_algorithm: str = Field("HS256", alias="JWT_ALGORITHM")

    # OpenAI settings
    openai_api_key: str = Field(..., alias="OPENAI_API_KEY")
    openai_base_url: str = Field("https://api.openai.com/v1", alias="OPENAI_BASE_URL")
    openai_model_id: str = Field("gpt-4o-mini", alias="OPENAI_MODEL_ID")

    # Bot settings
    bot_name: str = Field("PR Guardian AI", alias="BOT_NAME")
    log_level: str = Field("info", alias="LOG_LEVEL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
