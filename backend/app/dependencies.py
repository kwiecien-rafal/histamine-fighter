from fastapi import Request

from app.llm.base import LLMClient
from app.llm.config import LLMRequestConfig
from app.llm.factory import build_llm_client


def llm_dependency(request: Request) -> LLMClient:
    return build_llm_client(LLMRequestConfig.from_headers(request))
