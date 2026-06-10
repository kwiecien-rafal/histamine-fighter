from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    debug: bool = True
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    public_deployment: bool = False

    # Per-IP ceiling for the LLM-backed endpoints (the ones that cost money).
    rate_limit_per_minute: int = 10

    # How long a cached Learn answer stays valid. Re-seeding the knowledge
    # corpus clears the cache regardless.
    learn_cache_ttl_days: int = 7

    # Database connection. Default points at the Postgres in docker-compose.
    database_url: str = (
        "postgresql+asyncpg://histamine:histamine@localhost:5432/histamine"
    )

    llm_provider: str = "ollama"

    # Fixed for the whole corpus: stored and query vectors must share one model.
    embedding_backend: str = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "gpt-oss:20b"

    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    gemini_api_key: str | None = None
    openrouter_api_key: str | None = None


settings = Settings()
