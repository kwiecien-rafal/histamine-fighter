from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.enums import HistamineMechanism, SafetyLevel

# Hard cap on a confirmed ingredient list; the propose step trims to it too.
MAX_CONFIRMED_INGREDIENTS = 25
# Per-item cap, well under the ingredient service's query limit so a schema-valid
# name is never rejected downstream.
MAX_INGREDIENT_CHARS = 80


class DishLookupRequest(BaseModel):
    dish: str = Field(min_length=1, max_length=200)


class ProposedIngredientDraft(BaseModel):
    """One ingredient as the model drafts it — the propose call's structured-output item.

    Deliberately unconstrained: providers do not reliably honor length limits in
    structured-output schemas, and a sloppy item must degrade in code rather than
    fail the parse. The field descriptions are model-facing; the agent normalizes
    drafts into :class:`ProposedIngredient`.
    """

    name: str = Field(
        description="A single ingredient name, e.g. 'parmesan' — never a phrase.",
    )
    category: str | None = Field(
        default=None,
        description="Short food-group + preparation descriptor, e.g. 'aged hard cheese'.",
    )


class ProposedIngredients(BaseModel):
    """Structured output of the propose call: the drafted ingredient list and nothing else."""

    ingredients: list[ProposedIngredientDraft]


class ProposedIngredient(BaseModel):
    """One proposed ingredient as the API returns it, normalized from the model's draft."""

    name: str = Field(min_length=1, max_length=MAX_INGREDIENT_CHARS)
    category: str | None = Field(default=None, max_length=MAX_INGREDIENT_CHARS)


class IngredientProposalResponse(BaseModel):
    """The proposed ingredient list the user reviews and edits before assessment."""

    dish: str = Field(description="The dish text the proposal was made for.")
    ingredients: list[ProposedIngredient]
    model: str = Field(description="Which model proposed the ingredients.")


class ConfirmedIngredient(BaseModel):
    """One ingredient of the user-confirmed list sent for assessment.

    Names are stripped before the length check, so a whitespace-only name fails
    as the blank it is (422) instead of flowing in as an errored lookup that
    silently floors the verdict.
    """

    name: str = Field(min_length=1, max_length=MAX_INGREDIENT_CHARS)
    category: str | None = Field(default=None, max_length=MAX_INGREDIENT_CHARS)

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("category", mode="before")
    @classmethod
    def _normalize_category(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return value.strip() or None


class DishAssessmentRequest(BaseModel):
    dish: str = Field(min_length=1, max_length=200)
    ingredients: list[ConfirmedIngredient] = Field(
        min_length=1, max_length=MAX_CONFIRMED_INGREDIENTS
    )


class IngredientAssessment(BaseModel):
    """The index's reading of one confirmed ingredient, for per-ingredient badges."""

    name: str = Field(description="The confirmed ingredient name, echoed back.")
    safety: SafetyLevel = Field(description="Risk the index records for this ingredient.")
    found: bool = Field(
        description="False when the index has no entry for it, or its lookup failed."
    )
    error: bool = Field(
        default=False,
        description="True when the lookup failed: safety is then a cautious default, "
        "not an index reading.",
    )
    matched_on: Literal["ingredient", "category"] | None = Field(
        default=None, description="How the index matched it, when it did."
    )
    mechanisms: list[HistamineMechanism] = Field(
        default_factory=list,
        description="Why it is risky, from its most severe index reading.",
    )


class Replacement(BaseModel):
    """One ingredient to swap out, and what to use instead."""

    ingredient: str = Field(description="The high-histamine ingredient to replace.")
    swap: str = Field(description="The histamine-safe ingredient to use instead.")
    reason: str = Field(description="Why the swap helps.")


class DishExplanation(BaseModel):
    """The prose the model writes for a dish lookup.

    The model does not decide the verdict: that is computed in code from the
    curated index. The model only identifies the dish and writes the explanation
    and swaps that justify the verdict it is given. It sets ``dish`` to the dish
    it found in the message, not a copy of the raw input, so extra text like
    "what is 2+2?" gets ignored.
    """

    dish: str = Field(description="The dish found in the user's message.")
    explanation: str = Field(description="Short reason for the verdict.")
    replacements: list[Replacement] = Field(
        default_factory=list,
        description="Safer swaps for the flagged ingredients. Empty when the verdict is 'safe'.",
    )


class DishAssessmentResponse(DishExplanation):
    """The assessed dish: code-derived verdict, per-ingredient readings, prose."""

    verdict: SafetyLevel = Field(description="Overall histamine safety of the dish.")
    ingredients: list[IngredientAssessment] = Field(
        description="One index reading per confirmed ingredient."
    )
    model: str = Field(description="Which model produced the explanation.")
