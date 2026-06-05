"""Shared domain enums.

These live outside the ORM and schema layers so both can depend on the same
values without depending on each other.
"""

from enum import StrEnum


class SafetyLevel(StrEnum):
    """Histamine safety verdict shown for a whole dish."""

    SAFE = "safe"
    DEPENDS = "depends"
    AVOID = "avoid"


class Compatibility(StrEnum):
    """How well an ingredient is tolerated, by symptom severity.

    A missing value (NULL in the database) means there is no reliable rating,
    so the agent must not assert a safety level.
    """

    WELL_TOLERATED = "well_tolerated"
    MODERATELY_COMPATIBLE = "moderately_compatible"
    INCOMPATIBLE = "incompatible"
    POORLY_TOLERATED = "poorly_tolerated"


class CompatibilityVerdict(StrEnum):
    """Per-ingredient verdict reported to clients.

    The four rated levels mirror Compatibility; UNKNOWN replaces a missing
    rating so the API never returns null and a client cannot read "unrated" as
    "safe".
    """

    WELL_TOLERATED = "well_tolerated"
    MODERATELY_COMPATIBLE = "moderately_compatible"
    INCOMPATIBLE = "incompatible"
    POORLY_TOLERATED = "poorly_tolerated"
    UNKNOWN = "unknown"

    @classmethod
    def from_compatibility(cls, value: Compatibility | None) -> "CompatibilityVerdict":
        """Map a stored compatibility (or its absence) to a reported verdict."""
        return cls.UNKNOWN if value is None else cls(value)


class HistamineMechanism(StrEnum):
    """Why an ingredient may trigger symptoms (an ingredient can have several)."""

    PERISHABLE = "perishable"
    HIGH_HISTAMINE = "high_histamine"
    OTHER_AMINES = "other_amines"
    LIBERATOR = "liberator"
    DAO_BLOCKER = "dao_blocker"


class MatchType(StrEnum):
    """How a lookup matched an ingredient in the index."""

    EXACT = "exact"
    ALIAS = "alias"
    FUZZY = "fuzzy"
