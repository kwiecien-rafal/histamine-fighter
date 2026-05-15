from app.config import settings
from app.llm.base import LLMClient
from app.llm.mock_client import MockLLMClient


def get_llm_client() -> LLMClient:
    provider = settings.llm_provider
    if provider == "mock":
        return MockLLMClient()
    raise ValueError(f"Unsupported LLM provider: {provider}")
