"""Transactional email over the Resend API.

The only email the app sends is the magic-link login message. Without a Resend
key (dev, self-hosted) the message is written to the log instead, so
passwordless login works with zero external services: the operator copies the
URL or code from the log line.
"""

import httpx
import structlog

from app.config import settings
from app.core.logging import mask_email

log = structlog.get_logger(__name__)

RESEND_ENDPOINT = "https://api.resend.com/emails"


class EmailDeliveryError(Exception):
    """Resend refused or failed to accept the message. Mapped to 502."""


class EmailService:
    """Sends account emails. Stateless apart from the injected HTTP client."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def send_magic_link(self, email: str, *, link_url: str, code: str) -> None:
        """Email a sign-in link and its 6-digit code, or log them without a key.

        Raises:
            EmailDeliveryError: Resend rejected the request or was unreachable.
        """
        if settings.resend_api_key is None:
            # Dev/self-hosted mode: the log line is the email. The link and code are
            # login credentials, but short-lived and single-use, and anyone reading
            # this log already owns the machine. The address is still masked: the
            # URL and code are what the operator needs, the address is just PII.
            log.info("email.magic_link.dev", email=mask_email(email), url=link_url, code=code)
            return
        try:
            response = await self._client.post(
                RESEND_ENDPOINT,
                headers={"Authorization": f"Bearer {settings.resend_api_key}"},
                json={
                    "from": settings.email_from,
                    "to": [email],
                    "subject": f"Your Histamine Fighter sign-in code: {code}",
                    "html": _magic_link_html(link_url, code),
                    "text": _magic_link_text(link_url, code),
                },
            )
        except httpx.HTTPError as exc:
            log.warning("email.magic_link.unreachable", email=mask_email(email), error=str(exc))
            raise EmailDeliveryError("Could not reach the email service.") from exc
        if response.status_code >= 400:
            # Resend's error body can describe our own config (bad sender domain),
            # so it is logged for the operator but never echoed to the caller.
            log.warning(
                "email.magic_link.rejected",
                email=mask_email(email),
                status=response.status_code,
                body=response.text,
            )
            raise EmailDeliveryError("The email service rejected the message.")
        log.info("email.magic_link.sent", email=mask_email(email))


def _magic_link_text(link_url: str, code: str) -> str:
    minutes = settings.magic_link_ttl_minutes
    return (
        f"Sign in to Histamine Fighter:\n\n{link_url}\n\n"
        f"Or enter this code on the login page: {code}\n\n"
        f"The link and code expire in {minutes} minutes. "
        "If you didn't request this, you can ignore this email."
    )


def _magic_link_html(link_url: str, code: str) -> str:
    minutes = settings.magic_link_ttl_minutes
    return f"""\
<div style="font-family: sans-serif; max-width: 28rem; margin: 0 auto;">
  <h2 style="color: #1f3d2b;">Sign in to Histamine Fighter</h2>
  <p>
    <a href="{link_url}"
       style="display: inline-block; background: #1f3d2b; color: #ffffff;
              padding: 10px 18px; border-radius: 6px; text-decoration: none;">
      Sign in
    </a>
  </p>
  <p>Or enter this code on the login page:</p>
  <p style="font-size: 1.5rem; letter-spacing: 0.3em; font-weight: bold;">{code}</p>
  <p style="color: #666;">
    The link and code expire in {minutes} minutes.
    If you didn't request this, you can ignore this email.
  </p>
</div>
"""
