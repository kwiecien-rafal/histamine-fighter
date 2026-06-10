"""Per-IP rate limiting for the public API (slowapi).

One process-wide ``Limiter``; routes opt in with ``@limiter.limit(...)``. Only
the LLM-backed endpoints are limited for now — they are the ones that spend
money. The limit is read from settings per request so it stays configurable via
``RATE_LIMIT_PER_MINUTE`` without re-decorating.

Behind a reverse proxy the remote address is the proxy's; real client IPs need
the proxy to set the forwarding headers and uvicorn's ``--proxy-headers``.
Edge rate limiting (Cloudflare) is a separate, additional layer.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings

limiter = Limiter(key_func=get_remote_address)


def llm_rate_limit() -> str:
    """The shared per-IP limit for endpoints that invoke a language model."""
    return f"{settings.rate_limit_per_minute}/minute"
