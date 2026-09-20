"""Langfuse tracing seam: one callback handler, or none when unconfigured."""

from functools import lru_cache

import structlog
from langchain_core.runnables import RunnableConfig
from langfuse import Langfuse
from langfuse.langchain import CallbackHandler

from app.config import settings

log = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def _handler() -> CallbackHandler | None:
    """Build the process-wide Langfuse handler, or None when keys are unset."""
    public_key, secret_key = settings.langfuse_public_key, settings.langfuse_secret_key
    if public_key is None or secret_key is None:
        return None
    Langfuse(public_key=public_key, secret_key=secret_key, host=settings.langfuse_host)
    log.info("observability.langfuse_enabled", host=settings.langfuse_host)
    return CallbackHandler()


def trace_config(step: str) -> RunnableConfig:
    """Invoke config naming this call and routing it to Langfuse when configured."""
    config: RunnableConfig = {"run_name": step}
    handler = _handler()
    if handler is not None:
        config["callbacks"] = [handler]
    return config
