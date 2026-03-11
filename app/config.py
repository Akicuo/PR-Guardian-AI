from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    github_token: str = Field(..., alias="GITHUB_TOKEN")
    github_webhook_secret: str = Field("", alias="GITHUB_WEBHOOK_SECRET")
    openai_api_key: str = Field(..., alias="OPENAI_API_KEY")
    openai_base_url: str = Field("https://api.openai.com/v1", alias="OPENAI_BASE_URL")
    openai_model_id: str = Field("gpt-4o-mini", alias="OPENAI_MODEL_ID")
    bot_name: str = Field("PR Guardian AI", alias="BOT_NAME")
    log_level: str = Field("info", alias="LOG_LEVEL")
    review_chunk_chars: int = Field(30000, alias="REVIEW_CHUNK_CHARS")
    review_context_lines: int = Field(20, alias="REVIEW_CONTEXT_LINES")
    review_max_output_tokens: int = Field(1200, alias="REVIEW_MAX_OUTPUT_TOKENS")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore", 
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
