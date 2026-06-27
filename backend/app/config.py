from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The .env lives at the repo root, but scripts run with the working directory set
# to backend/ (uv run --directory backend ...), so a relative path would miss it.
ROOT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

# Obvious placeholder so local dev and tests boot without a secret. Production
# (PUBLIC_DEPLOYMENT or DEBUG off) is refused while this is still in place (see
# the validator below), so it can never stand in for a real production secret.
DEV_SECRET_KEY = "dev-secret-change-me-not-for-production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_ENV_FILE, extra="ignore")

    debug: bool = True
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    public_deployment: bool = False

    # Admin auth: signs the JWT issued at /admin/auth/login. Sourced from the
    # environment in production; the dev placeholder is rejected there.
    secret_key: str = DEV_SECRET_KEY
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # Browser session cookie carrying the admin JWT. Set httpOnly by the route so
    # XSS cannot read it. The Secure/SameSite flags and lifetime come from below.
    session_cookie_name: str = "hf_session"

    # Per-IP ceiling for the LLM-backed endpoints (the ones that cost money).
    rate_limit_per_minute: int = 10

    # Tighter per-IP ceiling on admin credential checks, to blunt brute force.
    auth_rate_limit_per_minute: int = 5

    # How long a cached Learn answer stays valid. Re-seeding the knowledge
    # corpus clears the cache regardless.
    learn_cache_ttl_days: int = 7

    # Hour the daily board unlocks, applied by the generation script when it stamps
    # each suggestion's reveal time. Deliberately UTC, not local: the board reveals
    # at the same instant for every visitor worldwide.
    daily_reveal_hour_utc: int = Field(default=10, ge=0, le=23)

    # How many days ahead, starting tomorrow, the nightly cron keeps the board filled.
    # 1 preserves the "just tomorrow" cadence; raise for a longer auto-runway.
    daily_cron_horizon_days: int = Field(default=1, ge=1, le=14)

    # The furthest ahead, in days from today, an admin may manually queue a board.
    daily_queue_max_ahead_days: int = Field(default=14, ge=1, le=90)

    # Database connection. Default points at the Postgres in docker-compose.
    database_url: str = "postgresql+asyncpg://histamine:histamine@localhost:5432/histamine"

    llm_provider: str = "ollama"

    # Composer sampling temperature, shared by the cron, the headless script, and the
    # live admin demo so they cannot drift apart. Creative enough that meals vary run
    # to run; safety never rides on the sampler, it is gated in code against the index.
    compose_temperature: float = 0.4

    # Fixed for the whole corpus: stored and query vectors must share one model.
    embedding_backend: str = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "gpt-oss:20b"

    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    gemini_api_key: str | None = None
    openrouter_api_key: str | None = None

    @property
    def is_production(self) -> bool:
        """Whether this runs as a production deployment.

        Any production signal counts: PUBLIC_DEPLOYMENT, or DEBUG off (CLAUDE
        section 20 mandates DEBUG=false in production). The secret-key gate and the
        Secure-cookie gate both read this, so they cannot disagree on what counts
        as production if one flag is later forgotten.
        """
        return self.public_deployment or not self.debug

    @property
    def cookie_secure(self) -> bool:
        """Whether the session cookie is restricted to HTTPS.

        Keyed on public_deployment, the only flag that implies TLS (terminated at the
        proxy in production, the same signal HSTS uses). Deliberately not is_production:
        DEBUG governs error verbosity, not transport, so keying Secure on it would make
        the cookie silently fail to set when an operator runs with DEBUG off over http.
        """
        return self.public_deployment

    @property
    def session_cookie_max_age(self) -> int:
        """Session cookie lifetime in seconds, matched to the JWT it carries so the
        two expire together."""
        return self.access_token_expire_minutes * 60

    @model_validator(mode="after")
    def _validate_secret(self) -> "Settings":
        """Require a strong admin secret in production, failing fast at startup."""
        # A blank SECRET_KEY is treated as unset so a copied .env.example falls
        # back to the placeholder instead of signing tokens with an empty key.
        if not self.secret_key.strip():
            self.secret_key = DEV_SECRET_KEY
        if self.is_production and (self.secret_key == DEV_SECRET_KEY or len(self.secret_key) < 32):
            raise ValueError(
                "SECRET_KEY must be a strong, non-default value (>=32 chars) in "
                "production (PUBLIC_DEPLOYMENT=true or DEBUG=false)."
            )
        return self


settings = Settings()
