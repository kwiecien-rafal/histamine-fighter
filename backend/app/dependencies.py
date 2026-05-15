from app.llm.base import LLMClient
from app.llm.factory import get_llm_client


def llm_dependency() -> LLMClient:
    return get_llm_client()
