"""Cloudflare Turnstile verification for the magic-link request form.

Turnstile is the scripted-signup gate: the browser widget issues a token the
backend must verify against Cloudflare before sending a login email. Without a
configured secret (dev, self-hosted) verification is skipped entirely, matching
the frontend, which only renders the widget when its site key is set.
"""

import httpx
import structlog

from app.config import settings

log = structlog.get_logger(__name__)

VERIFY_ENDPOINT = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


class TurnstileError(Exception):
    """The Turnstile challenge was missing, invalid, or unverifiable. Mapped to 400."""


async def verify_turnstile(client: httpx.AsyncClient, token: str | None, ip: str) -> None:
    """Check a Turnstile response token with Cloudflare, or no-op when unconfigured.

    Raises:
        TurnstileError: no token was sent, or Cloudflare did not confirm it.
    """
    if settings.turnstile_secret_key is None:
        return
    if not token:
        raise TurnstileError("Turnstile verification is required.")
    try:
        response = await client.post(
            VERIFY_ENDPOINT,
            data={
                "secret": settings.turnstile_secret_key,
                "response": token,
                "remoteip": ip,
            },
        )
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        # Fail closed: an unverifiable challenge is refused, not waved through,
        # since this gate exists precisely for when the site is under scripted load.
        log.warning("turnstile.unreachable", error=str(exc))
        raise TurnstileError("Could not verify the Turnstile challenge.") from exc
    if not isinstance(body, dict) or body.get("success") is not True:
        codes = body.get("error-codes") if isinstance(body, dict) else None
        log.info("turnstile.rejected", client=ip, codes=codes)
        raise TurnstileError("Turnstile verification failed.")
