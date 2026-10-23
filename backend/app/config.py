"""Настройки приложения из переменных окружения."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://gosdoc:gosdoc@db:5432/gosdoc"
    secret_key: str = Field(min_length=32)
    session_ttl_minutes: int = 12 * 60
    cookie_secure: bool = False

    # Первый администратор создаётся при старте, если пользователей нет.
    admin_username: str | None = None
    admin_password: str | None = None

    # Любой OpenAI-совместимый сервер: Ollama, vLLM, llama.cpp, LM Studio, облако.
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "local"
    llm_model: str = "qwen3:14b"
    llm_temperature: float = 0.2
    llm_timeout_seconds: float = 600
    # Дополнительные поля тела запроса, например {"reasoning_effort": "none"}.
    llm_extra_body: dict = {}


@lru_cache
def get_settings() -> Settings:
    return Settings()
