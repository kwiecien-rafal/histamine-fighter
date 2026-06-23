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


class CulinaryRole(StrEnum):
    """How much of the dish's identity rests on an ingredient."""

    CORE = "core"
    SUPPORTING = "supporting"
    SEASONING = "seasoning"


class AdaptationAction(StrEnum):
    """What to do about a flagged ingredient when adapting a dish."""

    SWAP = "swap"
    OMIT = "omit"
    NO_SAFE_SWAP = "no_safe_swap"


class DishIntegrity(StrEnum):
    """Whether a dish keeps its identity after its adaptations are applied.

    ``altered`` sits between the two extremes: a core ingredient had to change but
    a workable version remains, so the pivot is offered with softer wording than
    the dead end ``lost`` describes.
    """

    PRESERVED = "preserved"
    ALTERED = "altered"
    LOST = "lost"


class AlternativeGoal(StrEnum):
    """What the user is after when a dish cannot be adapted."""

    ANY_MEAL = "any_meal"
    SAME_STYLE = "same_style"
    SIMILAR_FLAVOURS = "similar_flavours"


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


class TraceReading(StrEnum):
    """One ingredient's reading on a composer trace step.

    The dish-level safety values, plus the two states the index cannot rate: a row
    it could not read, and a name it has no entry for. A stable token the frontend
    maps to a label, never raw branded copy.
    """

    SAFE = "safe"
    DEPENDS = "depends"
    AVOID = "avoid"
    UNVERIFIABLE = "unverifiable"
    NOT_INDEXED = "not_indexed"


class MealType(StrEnum):
    """Which meal of the day a curated suggestion is for."""

    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"
    SNACK = "snack"


class ApprovalStatus(StrEnum):
    """Whether a composed meal has cleared human review for the public pool.

    A meal is pool-eligible only once an admin moves it to ``approved``; until
    then membership cannot be read as the verified-safe signal it stands for.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Role(StrEnum):
    """An account's privilege level, read from the database on every request.

    Stored as a neutral domain value (CLAUDE section 19). New accounts default to
    ``USER`` for least privilege, and ``ADMIN`` is granted only by the create_admin
    CLI.
    """

    USER = "user"
    ADMIN = "admin"
