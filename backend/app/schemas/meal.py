from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from app.enums import (
    AdaptationAction,
    AlternativeGoal,
    CulinaryRole,
    DishIntegrity,
    HistamineMechanism,
    MealType,
    SafetyLevel,
)
from app.schemas.usage import LLMUsage

# Hard cap on a confirmed ingredient list; the propose step trims to it too.
MAX_CONFIRMED_INGREDIENTS = 25
# Per-item cap, well under the ingredient service's query limit so a schema-valid
# name is never rejected downstream.
MAX_INGREDIENT_CHARS = 80
MAX_DISH_CHARS = 200
MAX_REASON_CHARS = 240
MAX_ADVISORY_CHARS = 200
# An alternative's pitch; its name shares MAX_DISH_CHARS so a suggestion always
# fits back into DishLookupRequest when the user picks it. The alternatives
# prompt's inputs are free text, so these output-side caps — not the prompt — are
# the load-bearing bound on suggestion length and count; the agent clips to them.
MAX_PITCH_CHARS = 200
MAX_ALTERNATIVES = 3


class DishLookupRequest(BaseModel):
    dish: str = Field(min_length=1, max_length=MAX_DISH_CHARS)


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
    usage: LLMUsage = Field(description="Token usage of the model call behind this response.")


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
    dish: str = Field(min_length=1, max_length=MAX_DISH_CHARS)
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


class AdaptationDraft(BaseModel):
    """One adaptation as the model drafts it — the synthesis call's structured-output item.

    Deliberately unconstrained, like :class:`ProposedIngredientDraft`: the agent
    normalizes drafts into :class:`Adaptation` and drops what cannot be salvaged.
    The field descriptions are model-facing.
    """

    ingredients: list[str] = Field(
        default_factory=list,
        description="The covered ingredient names, copied exactly from the avoid "
        "list — several when they serve one culinary purpose, e.g. tomato and "
        "tomato paste. Never empty.",
    )
    role: str = Field(
        default="",
        description="The covered ingredients' role in this dish: 'core' (the dish "
        "is not itself without them), 'supporting', or 'seasoning'.",
    )
    action: str = Field(
        default="",
        description="'swap' only when the dish stays recognizably itself, 'omit' "
        "when it survives without the ingredient, else 'no_safe_swap'.",
    )
    swap: str | None = Field(
        default=None,
        description="Exactly one replacement ingredient, only when action is "
        "'swap' — never a list of options.",
    )
    reason: str = Field(
        default="",
        description="One line: why this keeps the dish working.",
    )


class AdvisoryDraft(BaseModel):
    """One depends-level note as the model drafts it; normalized into :class:`Advisory`."""

    ingredient: str = Field(default="", description="The flagged ingredient the note is about.")
    note: str = Field(
        default="",
        description="One short practical line grounded in the listed mechanisms.",
    )


class IngredientReadingDraft(BaseModel):
    """One ingredient's surviving index rows, as the disambiguation call drafts it.

    Unconstrained like the other drafts: the agent matches ``keep`` back against
    the rows it offered and ignores anything it did not, so an invented or
    misspelt name degrades to "kept nothing" rather than failing the parse.
    """

    ingredient: str = Field(default="", description="The ingredient name, copied from the list.")
    keep: list[str] = Field(
        default_factory=list,
        description="The index row names that genuinely denote this ingredient in "
        "the dish, copied exactly. Keep at least one.",
    )


class DisambiguationDraft(BaseModel):
    """Structured output of the disambiguation call: one reading per ambiguous ingredient."""

    readings: list[IngredientReadingDraft] = Field(default_factory=list)


class DishExplanationDraft(BaseModel):
    """The synthesis call's structured output.

    The model does not decide the verdict: that is computed in code from the
    curated index. The model only identifies the dish and writes the prose,
    adaptations and advisories that justify the verdict it is given. It sets
    ``dish`` to the dish it found in the message, not a copy of the raw input,
    so extra text like "what is 2+2?" gets ignored.
    """

    dish: str = Field(description="The dish found in the user's message.")
    explanation: str = Field(description="Short reason for the verdict.")
    adaptations: list[AdaptationDraft] = Field(
        default_factory=list,
        description="How to adapt the dish, one entry per culinary purpose, only "
        "for the avoid-level ingredients. Empty when the verdict is 'safe'.",
    )
    advisories: list[AdvisoryDraft] = Field(
        default_factory=list,
        description="One short note per depends-level ingredient. Never a swap.",
    )


class Adaptation(BaseModel):
    """One grounded adaptation entry: what to do about one culinary purpose."""

    ingredients: list[Annotated[str, StringConstraints(max_length=MAX_INGREDIENT_CHARS)]] = Field(
        min_length=1, description="The confirmed flagged ingredients this entry covers."
    )
    role: CulinaryRole = Field(description="The covered ingredients' role in this dish.")
    action: AdaptationAction = Field(description="What to do: swap, omit, or no safe swap.")
    swap: str | None = Field(
        default=None,
        max_length=MAX_INGREDIENT_CHARS,
        description="The replacement ingredient; present exactly when action is 'swap'.",
    )
    reason: str = Field(max_length=MAX_REASON_CHARS, description="Why this keeps the dish working.")

    @model_validator(mode="after")
    def _swap_matches_action(self) -> "Adaptation":
        if (self.action is AdaptationAction.SWAP) != (self.swap is not None):
            raise ValueError("swap must be present exactly when action is 'swap'")
        return self


class Advisory(BaseModel):
    """One depends-level ingredient's 'worth watching' note."""

    ingredient: str = Field(max_length=MAX_INGREDIENT_CHARS)
    note: str = Field(max_length=MAX_ADVISORY_CHARS)


class DishAssessmentResponse(BaseModel):
    """The assessed dish: code-derived verdict and integrity, grounded prose."""

    dish: str = Field(description="The dish found in the user's message.")
    verdict: SafetyLevel = Field(description="Overall histamine safety of the dish.")
    explanation: str = Field(description="Short reason for the verdict.")
    adaptations: list[Adaptation] = Field(
        description="How to adapt the dish, avoid-level ingredients only, grouped "
        "by culinary purpose. Empty when the verdict is 'safe'."
    )
    advisories: list[Advisory] = Field(
        description="Worth-watching notes for the depends-level ingredients."
    )
    integrity: DishIntegrity = Field(
        description="Whether the dish keeps its identity after the adaptations: "
        "'preserved', 'altered' when a core ingredient was swapped or omitted, or "
        "'lost' when a core ingredient has no safe swap."
    )
    ingredients: list[IngredientAssessment] = Field(
        description="One index reading per confirmed ingredient."
    )
    model: str = Field(description="Which model produced the explanation.")
    usage: LLMUsage = Field(description="Token usage of the model call(s) behind this response.")


_BoundedIngredientName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_INGREDIENT_CHARS),
]


class DishAlternativesRequest(BaseModel):
    """Ask for different dishes once the looked-up one cannot keep its identity.

    Repeated names in either list are deduped case-insensitively, first spelling
    wins, so a client cannot fill the prompt with copies of one ingredient.
    """

    dish: str = Field(min_length=1, max_length=MAX_DISH_CHARS)
    goal: AlternativeGoal
    avoid_ingredients: list[_BoundedIngredientName] = Field(
        min_length=1, max_length=MAX_CONFIRMED_INGREDIENTS
    )
    # The looked-up dish's own safe ingredients, used only to anchor suggestions
    # toward what already worked. Optional, and never touches a verdict.
    prefer_ingredients: list[_BoundedIngredientName] = Field(
        default_factory=list, max_length=MAX_CONFIRMED_INGREDIENTS
    )

    @field_validator("avoid_ingredients", "prefer_ingredients", mode="after")
    @classmethod
    def _dedupe_names(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        kept: list[str] = []
        for name in value:
            if name.casefold() not in seen:
                seen.add(name.casefold())
                kept.append(name)
        return kept


class AlternativeDraft(BaseModel):
    """One suggestion as the model drafts it; normalized into :class:`DishAlternative`."""

    name: str = Field(default="", description="A real, commonly recognized dish name.")
    pitch: str = Field(default="", description="One line of culinary appeal. No safety claims.")


class DishAlternativesDraft(BaseModel):
    """Structured output of the alternatives call: the drafted suggestions and nothing else."""

    alternatives: list[AlternativeDraft] = Field(default_factory=list)


class DishAlternative(BaseModel):
    """One suggested dish; its name fits :class:`DishLookupRequest` for re-lookup.

    ``source`` is a neutral domain value, not branded copy (CLAUDE section 19):
    ``verified`` is a member of the approved pool (code-verified and admin-approved,
    so the claim is sound), ``generated`` is a fresh idea the user re-vets on click.
    It defaults to ``generated`` so a caller that does not set it makes no safety
    claim.
    """

    name: str = Field(min_length=1, max_length=MAX_DISH_CHARS)
    pitch: str = Field(max_length=MAX_PITCH_CHARS)
    source: Literal["verified", "generated"] = "generated"


class DishAlternativesResponse(BaseModel):
    """Alternative dish ideas; each is only vetted once the user looks it up."""

    dish: str = Field(description="The dish the alternatives stand in for.")
    goal: AlternativeGoal = Field(description="The goal the suggestions were made for.")
    alternatives: list[DishAlternative] = Field(max_length=MAX_ALTERNATIVES)
    model: str = Field(description="Which model suggested the alternatives.")
    usage: LLMUsage = Field(description="Token usage of the model call behind this response.")


# --- Composer: the agentic meal-composition loop --------------------------------


class TraceEvent(BaseModel):
    """One authored step of the composer's reasoning, for the showcase replay.

    Written for a human watching the agent think, not raw tool JSON: ``text`` is a
    plain-language line and ``kind`` drives the animation's styling. The ``reject``
    events ("parmesan is avoid, dropping it") are the demo payoff.
    """

    kind: Literal["draft", "check", "swap", "reject", "submit", "verify"]
    text: str
    ingredient: str | None = None
    compatibility: str | None = None


class ComposedMeal(BaseModel):
    """A meal the composer built and code verified, before admin review.

    No per-meal verdict travels here: nothing the index flags survived (or the
    meal was never returned), so safety is carried by construction plus admin
    approval, not a field. ``unverified_ingredients`` are the ones absent from the
    index, accepted by the automated gate but surfaced so the reviewing admin
    closes that gap with eyes open rather than the gate hiding it.
    """

    name: str
    meal_type: MealType
    description: str
    ingredients: list[ProposedIngredient]
    recipe: list[str] | None
    tags: list[str]
    unverified_ingredients: list[str] = Field(default_factory=list)
    reasoning_trace: list[TraceEvent]
    model: str


class LookupIngredientSafety(BaseModel):
    """Look up one ingredient's histamine compatibility in the curated index."""

    ingredient: str = Field(
        description="A single ingredient name like 'parmesan', not a phrase or dish."
    )
    category: str | None = Field(
        default=None,
        description="Optional food-group and preparation descriptor for the fallback, "
        "e.g. 'aged hard cheese'.",
    )


class FindSafeIngredients(BaseModel):
    """List well-tolerated ingredients in a food category, as safe building blocks."""

    category: str = Field(
        description="A food-group and preparation descriptor, e.g. 'fresh vegetable'."
    )


class SearchCuratedMeals(BaseModel):
    """Search already-approved meals for inspiration and to avoid near-duplicates."""

    query: str = Field(
        description="A dish idea or flavour description to find similar approved meals."
    )
    meal_type: MealType | None = Field(
        default=None, description="Optionally restrict the search to one meal type."
    )


class SubmitMeal(BaseModel):
    """Submit the finished meal once every ingredient is verified index-safe."""

    name: str = Field(description="The dish name, short and appetising.")
    description: str = Field(description="One or two sentences describing the meal.")
    ingredients: list[ProposedIngredientDraft] = Field(
        description="Every ingredient, each with a short food-group and preparation category."
    )
    recipe: list[str] = Field(default_factory=list, description="Ordered preparation steps.")
    tags: list[str] = Field(default_factory=list, description="A few short descriptive tags.")
