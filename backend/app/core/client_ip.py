"""Single source of truth for the requester's IP address and its abuse key.

Everything that keys on the client address (the slowapi burst limiter, the daily
quota counters, signup stamping, auth logs) must agree on what that address is,
so they all read it from here. The value comes from ``request.client``, which is
correct behind a reverse proxy only when uvicorn runs with ``--proxy-headers``
and ``--forwarded-allow-ips`` set to the proxy; the app never parses
X-Forwarded-For itself, since a spoofable header here would let one client mint
unlimited quota identities.

Rate and quota keys go through ``ip_bucket``: an IPv6 subscriber typically holds
an entire /64 and can hop addresses inside it freely, so counting per full
address would make every per-IP cap free to bypass. Bucketing at /64 treats the
allocation, not the address, as the identity — the same choice Cloudflare and
most rate limiters make. IPv4 addresses are the allocation already and pass
through unchanged. Logs and ``created_from_ip`` keep the exact address.
"""

from ipaddress import AddressValueError, IPv6Address, IPv6Network, ip_address

import structlog
from fastapi import Request

from app.config import settings

log = structlog.get_logger(__name__)

_unproxied_reported = False


def client_ip(request: Request) -> str:
    """Return the requester's exact IP, or ``"unknown"`` when the transport has none."""
    return request.client.host if request.client else "unknown"


def warn_if_unproxied(request: Request) -> None:
    """Warn once if a public deployment looks to be missing its proxy headers.

    A real external client can never arrive as a loopback address, so seeing one on
    a public deployment means uvicorn is reading its own socket peer instead of the
    proxy's forwarded client, which collapses every caller into one abuse bucket
    (the per-IP send/signup/shared caps then apply site-wide). Best effort: it only
    catches the common same-host reverse-proxy case, but that is the one that fails
    silently, and there is no config-time signal for it.
    """
    global _unproxied_reported
    if _unproxied_reported or not settings.public_deployment or request.client is None:
        return
    try:
        is_loopback = ip_address(request.client.host).is_loopback
    except ValueError:
        return
    if is_loopback:
        _unproxied_reported = True
        log.warning(
            "client_ip.unproxied",
            host=request.client.host,
            hint="loopback client on a public deployment; run uvicorn with "
            "--proxy-headers and --forwarded-allow-ips set to the proxy, or every "
            "caller shares one rate-limit and quota bucket.",
        )


def ip_bucket(ip: str) -> str:
    """Collapse an IP to its abuse-control identity: the /64 for IPv6, itself otherwise.

    Unparseable input (including ``"unknown"``) passes through unchanged, so a
    missing transport address still lands in one shared bucket instead of failing.
    """
    try:
        address = IPv6Address(ip)
    except AddressValueError:
        return ip
    # A dual-stack listener reports IPv4 clients as ::ffff:a.b.c.d; bucketing
    # those at /64 would merge every IPv4 user into one identity. Unmap instead.
    if address.ipv4_mapped is not None:
        return str(address.ipv4_mapped)
    return str(IPv6Network((address, 64), strict=False))
