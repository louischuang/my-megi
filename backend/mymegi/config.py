from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="local", alias="APP_ENV")
    database_url: str = Field(
        default="postgresql://mymegi:mymegi@localhost:5432/mymegi",
        alias="DATABASE_URL",
    )
    upload_dir: Path = Field(default=Path("uploads"), alias="UPLOAD_DIR")
    openai_base_url: str = Field(default="http://localhost:11434/v1", alias="OPENAI_BASE_URL")
    openai_api_key: str = Field(default="ollama", alias="OPENAI_API_KEY")
    llm_model: str = Field(default="gemma4:e4b", alias="LLM_MODEL")
    ocr_engine: str = Field(default="tesseract", alias="OCR_ENGINE")
    bootstrap_admin_email: str = Field(default="admin@mymegi.local", alias="BOOTSTRAP_ADMIN_EMAIL")
    bootstrap_admin_password: str = Field(default="mymegi-admin", alias="BOOTSTRAP_ADMIN_PASSWORD")
    bootstrap_admin_name: str = Field(default="My Megi Admin", alias="BOOTSTRAP_ADMIN_NAME")
    session_days: int = Field(default=7, alias="SESSION_DAYS")
    login_rate_limit: int = Field(default=10, alias="LOGIN_RATE_LIMIT")
    login_rate_window_seconds: int = Field(default=60, alias="LOGIN_RATE_WINDOW_SECONDS")
    api_token_create_rate_limit: int = Field(default=6, alias="API_TOKEN_CREATE_RATE_LIMIT")
    api_token_create_rate_window_seconds: int = Field(default=60, alias="API_TOKEN_CREATE_RATE_WINDOW_SECONDS")
    api_token_revoke_rate_limit: int = Field(default=20, alias="API_TOKEN_REVOKE_RATE_LIMIT")
    api_token_revoke_rate_window_seconds: int = Field(default=60, alias="API_TOKEN_REVOKE_RATE_WINDOW_SECONDS")


@lru_cache
def get_settings() -> Settings:
    return Settings()
