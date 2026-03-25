
from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    APP_NAME: str = "NEXUS"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    SECRET_KEY: str = "key"

    # Database
    DATABASE_URL: str = ""
    DATABASE_URL_SYNC: str = ""

    # Redis
    REDIS_URL: str = ""

    # ChromaDB
    CHROMA_HOST: str = "localhost"
    CHROMA_PORT: int = 8001

    # JWT
    JWT_SECRET: str = "jwt-secret-change-this-too"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # OpenAI
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o"
    OPENAI_MODEL_FAST: str = "gpt-4o-mini"

    # GitHub
    GITHUB_TOKEN: str = ""

    # Repos
    REPOS_DIR: str = "./repos"
    MAX_REPO_SIZE_MB: int = 100

    # Rate limiting
    RATE_LIMIT_PER_MINUTE: int = 10
    MAX_CONCURRENT_TASKS_PER_USER: int = 2

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


def get_llm_config(fast: bool = False):
    model = settings.OPENAI_MODEL_FAST if fast else settings.OPENAI_MODEL
    return {
        "model": model,
        "api_key": settings.OPENAI_API_KEY,
        "temperature": 0.1,
        "timeout": 180,
        "cache_seed": None,  # disable cache for production streaming
    }
