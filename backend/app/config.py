from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The .env lives at the repo root, but scripts run with the working directory set
# to backend/ (uv run --directory backend ...), so a relative path would miss it.
ROOT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

# Obvious placeholder so local dev and tests boot without a secret. A public
# deployment is refused while this is still in place (see the validator below),
# so it can never stand in for a real production secret. Kept >=32 chars to clear
# the HMAC key-length floor for HS256.
DEV_SECRET_KEY = "dev-secret-change-me-not-for-production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_ENV_FILE, extra="ignore")

    debug: bool = True
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    public_deployment: bool = False

    # Admin auth: signs the JWT issued at /admin/auth/login. Sourced from the
    # environment in production; the dev placeholder is rejected when public.
    secret_key: str = DEV_SECRET_KEY
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # Per-IP ceiling for the LLM-backed endpoints (the ones that cost money).
    rate_limit_per_minute: int = 10

    # How long a cached Learn answer stays valid. Re-seeding the knowledge
    # corpus clears the cache regardless.
    learn_cache_ttl_days: int = 7

    # Database connection. Default points at the Postgres in docker-compose.
    database_url: str = "postgresql+asyncpg://histamine:histamine@localhost:5432/histamine"

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

    @model_validator(mode="after")
    def _require_real_secret_when_public(self) -> "Settings":
        """Refuse to boot a public deployment that still uses the dev secret.

        Fails fast at startup rather than silently signing admin tokens with a
        key that ships in the repo. Local dev and tests keep the placeholder.
        """
        if self.public_deployment and (
            self.secret_key == DEV_SECRET_KEY or len(self.secret_key) < 32
        ):
            raise ValueError(
                "SECRET_KEY must be set to a strong, non-default value (>=32 chars) "
                "for a public deployment."
            )
        return self


settings = Settings()
