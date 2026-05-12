import os
from urllib.parse import quote_plus

from dotenv import load_dotenv

load_dotenv()


def _build_db_url() -> str:
    host = os.getenv("DB_HOST", "192.168.2.8")
    port = os.getenv("DB_PORT", "3306")
    user = os.getenv("DB_USER", "krauser")
    password = quote_plus(os.getenv("DB_PASSWORD", "vThink135#"))
    name = os.getenv("DB_NAME", "vthink_kra")
    return f"mysql+pymysql://{user}:{password}@{host}:{port}/{name}?charset=utf8mb4"


class Settings:
    DB_HOST: str = os.getenv("DB_HOST", "192.168.2.8")
    DB_PORT: int = int(os.getenv("DB_PORT", "3306"))
    DB_USER: str = os.getenv("DB_USER", "krauser")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")
    DB_NAME: str = os.getenv("DB_NAME", "vthink_kra")
    DATABASE_URL: str = os.getenv("DATABASE_URL") or _build_db_url()
    DB_POOL_SIZE: int = int(os.getenv("DB_POOL_SIZE", "10"))
    DB_MAX_OVERFLOW: int = int(os.getenv("DB_MAX_OVERFLOW", "20"))
    DB_ECHO: bool = os.getenv("DB_ECHO", "false").lower() == "true"

    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
    LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "2000"))
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0"))

    MAX_QUERY_TIMEOUT: int = int(os.getenv("MAX_QUERY_TIMEOUT", "30"))
    MAX_RESULT_ROWS: int = int(os.getenv("MAX_RESULT_ROWS", "1000"))
    DEFAULT_RESULT_LIMIT: int = int(os.getenv("DEFAULT_RESULT_LIMIT", "100"))
    MAX_RETRY_COUNT: int = int(os.getenv("MAX_RETRY_COUNT", "2"))

    MAX_CONVERSATION_HISTORY: int = int(os.getenv("MAX_CONVERSATION_HISTORY", "10"))

    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    DEBUG_MODE: bool = os.getenv("DEBUG_MODE", "false").lower() == "true"

    REDIS_URL: str = os.getenv("REDIS_URL", "")

    CACHE_TTL_SECONDS: int = int(os.getenv("CACHE_TTL_SECONDS", "3600"))
    CACHE_MAX_SIZE: int = int(os.getenv("CACHE_MAX_SIZE", "500"))

    STREAM_CACHE_TTL_SECONDS: int = int(os.getenv("STREAM_CACHE_TTL_SECONDS", "3600"))
    STREAM_REFRESH_INTERVAL_SECONDS: int = int(os.getenv("STREAM_REFRESH_INTERVAL_SECONDS", "30"))


settings = Settings()
