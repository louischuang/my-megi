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
    llm_model: str = Field(default="gemma4:26b", alias="LLM_MODEL")
    ocr_engine: str = Field(default="tesseract", alias="OCR_ENGINE")


@lru_cache
def get_settings() -> Settings:
    return Settings()
