"""Disposable email domain blocklist.

Magic-link login makes an email address the whole credential, so throwaway
inboxes would make account farming free. The vendored list (see the data file's
header for source and fetch date) is loaded once into a frozenset; refreshing it
is replacing the file.
"""

from functools import cache
from pathlib import Path

from app.models.user import normalize_email

_BLOCKLIST_PATH = Path(__file__).parent / "data" / "disposable_email_blocklist.txt"


@cache
def _blocked_domains() -> frozenset[str]:
    # Lowercase on load so the set matches the lowercased domains is_disposable
    # compares against; a refreshed list carrying any mixed-case entry would
    # otherwise silently never match. Comment/blank filtering is on the stripped
    # value, so an indented comment is dropped too.
    domains = (
        line.strip().lower() for line in _BLOCKLIST_PATH.read_text(encoding="utf-8").splitlines()
    )
    return frozenset(domain for domain in domains if domain and not domain.startswith("#"))


def warm_blocklist() -> None:
    """Load and cache the blocklist, so the first login request pays no file read."""
    _blocked_domains()


def is_disposable(email: str) -> bool:
    """Whether the email's domain is a known disposable-inbox provider.

    Subdomains of a blocked domain are blocked too (mail.mailinator.com), since
    disposable providers hand out arbitrary subdomains freely.
    """
    domain = normalize_email(email).rpartition("@")[2]
    if not domain:
        return False
    blocked = _blocked_domains()
    return any(candidate in blocked for candidate in _domain_suffixes(domain))


def _domain_suffixes(domain: str) -> list[str]:
    """All registrable suffixes of a domain: a.b.c -> [a.b.c, b.c]."""
    parts = domain.split(".")
    return [".".join(parts[i:]) for i in range(len(parts) - 1)]
