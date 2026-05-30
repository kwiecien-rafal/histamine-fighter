from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    debug: bool = True
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    public_deployment: bool = False

    llm_provider: str = "mock"

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "gpt-oss:20b"

    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    gemini_api_key: str | None = None
    openrouter_api_key: str | None = None


settings = Settings()
