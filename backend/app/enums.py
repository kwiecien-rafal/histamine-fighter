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
    """How well an ingredient is tolerated, on the SIGHI 0-3 scale.

    A missing value (NULL in the database) mirrors SIGHI's "-" and "?" markers:
    no reliable rating, so the agent must not assert a safety level.
    """

    WELL_TOLERATED = "well_tolerated"  # SIGHI 0
    MODERATELY_COMPATIBLE = "moderately_compatible"  # SIGHI 1
    INCOMPATIBLE = "incompatible"  # SIGHI 2
    POORLY_TOLERATED = "poorly_tolerated"  # SIGHI 3


class HistamineMechanism(StrEnum):
    """Why an ingredient may trigger symptoms, from the SIGHI mechanism flags."""

    PERISHABLE = "perishable"  # SIGHI "H!", rapid histamine formation when stale
    HIGH_HISTAMINE = "high_histamine"  # SIGHI "H"
    OTHER_AMINES = "other_amines"  # SIGHI "A"
    LIBERATOR = "liberator"  # SIGHI "L", releases mast cell mediators
    DAO_BLOCKER = "dao_blocker"  # SIGHI "B", blocks histamine-degrading enzymes
