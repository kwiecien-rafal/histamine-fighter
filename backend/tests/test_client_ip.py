"""ip_bucket: the /64 identity every per-IP abuse control keys on."""

import pytest

from app.core.client_ip import ip_bucket


def test_ipv4_passes_through_unchanged() -> None:
    assert ip_bucket("203.0.113.7") == "203.0.113.7"


def test_ipv6_collapses_to_its_64() -> None:
    # Two addresses in one subscriber allocation share a single quota identity.
    a = ip_bucket("2001:db8:1:2:aaaa:bbbb:cccc:dddd")
    b = ip_bucket("2001:db8:1:2:1111:2222:3333:4444")

    assert a == b == "2001:db8:1:2::/64"


def test_distinct_64s_stay_distinct() -> None:
    assert ip_bucket("2001:db8:1:2::1") != ip_bucket("2001:db8:1:3::1")


def test_ipv4_mapped_ipv6_unmaps_instead_of_bucketing() -> None:
    # A dual-stack listener reports IPv4 clients as ::ffff:a.b.c.d; they must
    # not all merge into the one mapped /64.
    assert ip_bucket("::ffff:203.0.113.7") == "203.0.113.7"


@pytest.mark.parametrize("raw", ["unknown", "", "not-an-ip"])
def test_unparseable_input_passes_through(raw: str) -> None:
    assert ip_bucket(raw) == raw
