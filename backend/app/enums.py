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


class HistamineMechanism(StrEnum):
    """Why an ingredient may trigger symptoms (an ingredient can have several)."""

    PERISHABLE = "perishable"
    HIGH_HISTAMINE = "high_histamine"
    OTHER_AMINES = "other_amines"
    LIBERATOR = "liberator"
    DAO_BLOCKER = "dao_blocker"
