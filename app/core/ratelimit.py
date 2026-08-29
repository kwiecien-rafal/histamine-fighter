"""Per-IP rate limiting for the public API (slowapi).

One process-wide ``Limiter``; routes opt in with ``@limiter.limit(...)``. Only
the LLM-backed endpoints are limited for now — they are the ones that spend
money. The limit is read from settings per request so it stays configurable via
``RATE_LIMIT_PER_MINUTE`` without re-decorating.

Behind a reverse proxy the remote address is the proxy's; real client IPs need
the proxy to set the forwarding headers and uvicorn's ``--proxy-headers``.
Edge rate limiting (Cloudflare) is a separate, additional layer.
"""

from fastapi import Request
from slowapi import Limiter

from app.config import settings
from app.core.client_ip import client_ip, ip_bucket


def _rate_limit_key(request: Request) -> str:
    """Key limits on the same /64-bucketed identity the daily quotas use."""
    return ip_bucket(client_ip(request))


limiter = Limiter(key_func=_rate_limit_key)


def llm_rate_limit() -> str:
    """The shared per-IP limit for endpoints that invoke a language model."""
    return f"{settings.rate_limit_per_minute}/minute"


def auth_rate_limit() -> str:
    """A tight per-IP limit on credential checks, to blunt password brute force."""
    return f"{settings.auth_rate_limit_per_minute}/minute"


def save_rate_limit() -> str:
    """The per-IP limit on saved-meal writes, roomy enough for a board of save taps."""
    return f"{settings.save_rate_limit_per_minute}/minute"
