"""Tests for the reported compatibility verdict mapping."""

from app.enums import Compatibility, CompatibilityVerdict


def test_missing_rating_maps_to_unknown() -> None:
    assert CompatibilityVerdict.from_compatibility(None) is CompatibilityVerdict.UNKNOWN


def test_rating_maps_to_matching_verdict() -> None:
    # from_compatibility does cls(value), so a Compatibility level with no matching
    # verdict would raise here. This turns that coupling into a CI failure instead
    # of a runtime 500 when such a row is requested.
    for level in Compatibility:
        assert CompatibilityVerdict.from_compatibility(level).value == level.value


def test_verdict_covers_the_compatibility_scale() -> None:
    # Guards against the two enums drifting apart.
    rated = {v.value for v in CompatibilityVerdict} - {CompatibilityVerdict.UNKNOWN.value}
    assert rated == {level.value for level in Compatibility}
