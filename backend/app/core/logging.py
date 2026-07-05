"""Logging setup.

configure_logging runs once at startup. After that, modules log through
structlog.get_logger(). Debug mode renders to the console for readable local
output, production renders JSON so the logs can be parsed downstream.
"""

import logging

import structlog
from structlog.typing import Processor

from app.config import settings


def mask_email(email: str) -> str:
    """Redact an email for log lines: ``gerald@example.com`` -> ``g***@example.com``.

    Accounts identify as ``user_id`` in logs; the masked form is for events with
    no account yet (or abuse warnings, where the domain is the useful part).
    Emails are personal data, and logs outlive the account-deletion promise.
    """
    local, _, domain = email.partition("@")
    if not domain:
        return "***"
    return f"{local[:1]}***@{domain}"


def configure_logging() -> None:
    """Configure structlog for the current process."""
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]
    renderer: Processor = (
        structlog.dev.ConsoleRenderer() if settings.debug else structlog.processors.JSONRenderer()
    )
    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if settings.debug else logging.INFO
        ),
        cache_logger_on_first_use=True,
    )
