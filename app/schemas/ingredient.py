"""Response schema for the public ingredient lookup endpoint."""

from pydantic import BaseModel, Field

from app.enums import CompatibilityVerdict, HistamineMechanism, MatchType


class IngredientCandidate(BaseModel):
    """One matched row from the curated index."""

    name: str = Field(description="Canonical name of the matched ingredient.")
    match_type: MatchType = Field(description="How it matched: exact, alias, or fuzzy.")
    score: float = Field(description="Match strength from 0 to 1 (1 for exact and alias matches).")
    compatibility: CompatibilityVerdict = Field(
        description="Tolerance verdict. 'unknown' means the ingredient is in the index but "
        "has no reliable rating; it is never null and must not be treated as safe."
    )
    mechanisms: list[HistamineMechanism] = Field(
        default_factory=list, description="Why the ingredient may trigger symptoms."
    )
    category: str | None = Field(default=None, description="Coarse grouping, e.g. cheese or fish.")
    notes: str | None = Field(default=None, description="Short plain-language note.")
    sources: list[str] = Field(default_factory=list, description="References behind the rating.")


class IngredientLookupResponse(BaseModel):
    """Candidate matches for a looked-up ingredient name.

    ``candidates`` is ordered best-first and is empty when nothing matched
    (unknown, not safe). ``ambiguous`` is true when the candidates disagree on
    compatibility, e.g. "egg" matching both egg yolk and egg white, which the
    caller (a person, or the dish agent with context) must resolve.
    """

    query: str = Field(description="The name that was looked up.")
    found: bool = Field(description="Whether any candidate matched.")
    ambiguous: bool = Field(description="Whether the candidates disagree on compatibility.")
    candidates: list[IngredientCandidate] = Field(default_factory=list)
