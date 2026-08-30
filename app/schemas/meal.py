from collections.abc import Iterable
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from app.enums import (
    AdaptationAction,
    AlternativeGoal,
    CulinaryRole,
    DishIntegrity,
    HistamineMechanism,
    MealType,
    RewriteOutcome,
    SafetyLevel,
    TraceReading,
)
from app.schemas.usage import LLMUsage

# Hard cap on a confirmed ingredient list; the propose step trims to it too, and the
# editor macro renders it as the row limit.
MAX_CONFIRMED_INGREDIENTS = 30
# Per-item cap, well under the ingredient service's query limit so a schema-valid
# name is never rejected downstream.
MAX_INGREDIENT_CHARS = 80
MAX_DISH_CHARS = 200
# Composed-meal caps, shared by the composer's normalization and the admin edit
# schemas so an edit can never store a meal the composer would not produce.
MAX_DESCRIPTION_CHARS = 1000
MAX_RECIPE_STEPS = 20
MAX_TAGS = 8
MAX_TAG_CHARS = 40
MAX_REASON_CHARS = 240
MAX_ADVISORY_CHARS = 200
# The one line a rewritten dish gets to admit what it gave up.
MAX_TRADE_OFF_CHARS = 200
# An alternative's pitch; its name shares MAX_DISH_CHARS so a suggestion always
# fits back into DishLookupRequest when the user picks it. The alternatives
# prompt's inputs are free text, so these output-side caps — not the prompt — are
# the load-bearing bound on suggestion length and count; the agent clips to them.
MAX_PITCH_CHARS = 200
MAX_ALTERNATIVES = 3
# The short "format and character" descriptor the synthesis step writes, used
# by the frontend to make the alternatives goal buttons specific.
MAX_DISH_STYLE_CHARS = 60


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

    recognized: bool = Field(
        default=True,
        description="False when the text names no real, identifiable dish; "
        "then the ingredient list must be empty.",
    )
    ingredients: list[ProposedIngredientDraft]


class ProposedIngredient(BaseModel):
    """One proposed ingredient as the API returns it, normalized from the model's draft."""

    name: str = Field(min_length=1, max_length=MAX_INGREDIENT_CHARS)
    category: str | None = Field(default=None, max_length=MAX_INGREDIENT_CHARS)


# The composer (on submit) and the admin edit schemas share these so a composed meal and
# an edited one are shaped by one set of rules. Both truncate rather than reject, so a
# freshly composed meal always round-trips back through an edit unchanged.


def normalize_dish_text(value: str, *, max_chars: int) -> str:
    """Strip a free-text meal field and cap its length."""
    return value.strip()[:max_chars].rstrip()


def lookup_source_key(dish: str) -> str:
    """The canonical key for a dish name, derived server-side.

    Keys the lookup caches. Saved meals used to share it, but now key on a
    client-minted per-result id so same-named results save separately.
    """
    return normalize_dish_text(dish, max_chars=MAX_DISH_CHARS).casefold()


def normalize_ingredients(items: Iterable[tuple[str, str | None]]) -> list[ProposedIngredient]:
    """Trim and cap each ingredient, drop blanks and case-folded duplicates, cap the count."""
    kept: list[ProposedIngredient] = []
    seen: set[str] = set()
    for raw_name, raw_category in items:
        name = raw_name.strip()[:MAX_INGREDIENT_CHARS].rstrip()
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        category = (raw_category or "").strip()[:MAX_INGREDIENT_CHARS].rstrip()
        kept.append(ProposedIngredient(name=name, category=category or None))
        if len(kept) == MAX_CONFIRMED_INGREDIENTS:
            break
    return kept


def normalize_recipe(steps: Iterable[str]) -> list[str] | None:
    """The trimmed recipe steps capped to the step limit, or None when none survive."""
    cleaned = [step.strip() for step in steps if step.strip()][:MAX_RECIPE_STEPS]
    return cleaned or None


def normalize_tags(tags: Iterable[str]) -> list[str]:
    """Trim and cap each tag, drop blanks and case-folded duplicates, cap the count."""
    kept: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        cleaned = tag.strip()[:MAX_TAG_CHARS].rstrip()
        if cleaned and cleaned.casefold() not in seen:
            seen.add(cleaned.casefold())
            kept.append(cleaned)
        if len(kept) == MAX_TAGS:
            break
    return kept


class RecipeGeneration(BaseModel):
    """One generated recipe: the normalized steps plus the call's provenance."""

    steps: list[str] = Field(min_length=1)
    model: str = Field(description="Which model wrote the recipe.")
    usage: LLMUsage = Field(description="Token usage of the model call behind the recipe.")


class RecipeDraft(BaseModel):
    """Structured output of the recipe call: the ordered steps and nothing else.

    Unconstrained like the other drafts; the agent normalizes the steps through
    :func:`normalize_recipe`, so a sloppy model degrades instead of failing the
    parse.
    """

    steps: list[str] = Field(
        default_factory=list,
        description="Ordered preparation steps, one clear action each.",
    )


class IngredientProposalResponse(BaseModel):
    """The proposed ingredient list the user reviews and edits before assessment."""

    dish: str = Field(description="The dish text the proposal was made for.")
    recognized: bool = Field(
        default=True,
        description="False when no dish was recognisable in the text; the client "
        "shows an announcement instead of the (empty) editor.",
    )
    ingredients: list[ProposedIngredient]
    model: str = Field(description="Which model proposed the ingredients.")
    cached: bool = Field(
        default=False,
        description="True when served from the lookup cache; usage is then zero "
        "and `model` names the model that produced the original proposal.",
    )
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
    dish_style: str = Field(
        default="",
        description="3-6 plain words for the dish's format and character, e.g. "
        "'hearty tomato pasta dish'. Empty when no dish was recognisable.",
    )
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


class CautionedIngredient(BaseModel):
    """A moderately compatible ingredient kept in a meal, with the index's guidance.

    The note is the curated index's own wording ("fresh only", "small amounts"),
    never model-written: the model may keep the ingredient, but only the index says
    how. A stable domain value pair (CLAUDE section 19); the frontend derives its
    caution styling from the field's presence.
    """

    name: str
    note: str


class LookupRecipeRequest(BaseModel):
    """Recipe request for an assessed dish the user has not saved.

    Client-asserted like a lookup save: the fields only shape the recipe text,
    and the agent still scans the drafted steps against the live index. The
    advisories are the assessment's own depends-level notes echoed back, so the
    steps can honour guidance like "fresh only".
    """

    dish: str = Field(min_length=1, max_length=MAX_DISH_CHARS)
    description: str = ""
    ingredients: list[ConfirmedIngredient] = Field(
        min_length=1, max_length=MAX_CONFIRMED_INGREDIENTS
    )
    advisories: list[Advisory] = Field(default_factory=list, max_length=MAX_CONFIRMED_INGREDIENTS)

    @field_validator("description", mode="before")
    @classmethod
    def _normalize_description(cls, value: object) -> object:
        # Truncate, don't reject: the client echoes model prose whose length it
        # does not control (same treatment as a lookup save's description).
        if not isinstance(value, str):
            return value
        return normalize_dish_text(value, max_chars=MAX_DESCRIPTION_CHARS)


class DishAssessmentResponse(BaseModel):
    """The assessed dish: code-derived verdict and integrity, grounded prose."""

    dish: str = Field(description="The dish found in the user's message.")
    dish_style: str | None = Field(
        default=None,
        max_length=MAX_DISH_STYLE_CHARS,
        description="Short model-written descriptor of the dish's format and "
        "character ('hearty tomato pasta dish'); presentation only, never part "
        "of the verdict.",
    )
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
    cached: bool = Field(
        default=False,
        description="True when served from the lookup cache after the verdict "
        "re-grounded identically against the live index; usage is then zero.",
    )
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


class DishRewriteRequest(BaseModel):
    """Ask for a version of this dish its ingredient list can actually support.

    Deliberately the same shape as :class:`DishAssessmentRequest`: the rewrite is
    always assessed first, so the caller states the dish and what is in it and
    nothing else. An assessment is never accepted from the client — it is
    recomputed (and cache-served) from these two fields, so a rewrite can never be
    steered by a verdict the caller made up.
    """

    dish: str = Field(min_length=1, max_length=MAX_DISH_CHARS)
    ingredients: list[ConfirmedIngredient] = Field(
        min_length=1, max_length=MAX_CONFIRMED_INGREDIENTS
    )


class IngredientChangeDraft(BaseModel):
    """One swap or removal as the model reports it; normalized into :class:`IngredientChange`."""

    original: str = Field(default="", description="The original ingredient this line accounts for.")
    replacement: str = Field(
        default="", description="What takes its place; empty when it is simply left out."
    )
    reason: str = Field(default="", description="One line the cook can act on.")


class AdaptedDishDraft(BaseModel):
    """The rewrite call's structured output: a whole dish, not a list of edits.

    The model returns the finished ingredient list because that is what code can
    check — every name is read from the index before any of this is shown, and a
    list of edits would have to be applied first to be checkable at all.
    """

    name: str = Field(default="", description="What to call this version of the dish.")
    explanation: str = Field(default="", description="Two or three sentences on the version.")
    ingredients: list[ProposedIngredientDraft] = Field(
        default_factory=list, description="The complete ingredient list of the new version."
    )
    changes: list[IngredientChangeDraft] = Field(
        default_factory=list, description="One line per original ingredient that changed or went."
    )
    trade_off: str = Field(
        default="", description="One honest line on what is lost. Empty when nothing is."
    )


class IngredientChange(BaseModel):
    """One grounded line of the diff: what left the dish, and what took its place.

    Membership is code-owned: ``original`` is always a name from the list that was
    assessed and ``replacement``, when set, is always a name on the rewritten list.
    The model supplies only ``reason``, so the diff cannot claim a substitution
    that is not actually in the dish.
    """

    original: str = Field(max_length=MAX_INGREDIENT_CHARS)
    replacement: str | None = Field(
        default=None,
        max_length=MAX_INGREDIENT_CHARS,
        description="The ingredient that replaced it, or null when it was dropped.",
    )
    reason: str = Field(default="", max_length=MAX_REASON_CHARS)


class AdaptedDish(BaseModel):
    """A version of a dish that the curated index can support, or why there is none.

    ``verdict`` is ``grounded_verdict`` over this response's own ingredient list —
    the identical rule the assess path applies, so the same list never reads two
    ways. It is not floored for ``unverified_ingredients``: an ingredient the index
    has no entry for carries no recorded risk anywhere else in the app, and those
    names are listed here instead of being folded into a verdict that would hide
    them. Nothing the index flags as avoid can appear at all — that is what the
    rewrite loop verifies before an outcome of ``adapted`` is possible.
    """

    dish: str = Field(description="The dish the visitor asked about.")
    name: str = Field(description="What to call this version; the original when unchanged.")
    outcome: RewriteOutcome = Field(description="How the attempt ended.")
    explanation: str = Field(description="Short reason for what came back.")
    ingredients: list[ProposedIngredient] = Field(
        default_factory=list, description="The new version's ingredients; empty when there is none."
    )
    changes: list[IngredientChange] = Field(
        default_factory=list, description="What changed, one line per original ingredient."
    )
    trade_off: str | None = Field(
        default=None,
        max_length=MAX_TRADE_OFF_CHARS,
        description="What the new version gives up, when it gives up anything.",
    )
    verdict: SafetyLevel = Field(description="The rewritten list's own grounded verdict.")
    unverified_ingredients: list[str] = Field(
        default_factory=list,
        description="Ingredients the index has no rating for: unknown, not safe.",
    )
    cautioned_ingredients: list[CautionedIngredient] = Field(
        default_factory=list,
        description="Depends-level ingredients kept, each with the index's own note.",
    )
    blocked_ingredients: list[str] = Field(
        default_factory=list,
        description="Why no version exists: the ingredients nothing could replace.",
    )
    model: str = Field(description="Which model wrote the version.")
    cached: bool = Field(
        default=False,
        description="True when served from the rewrite cache after re-grounding identically.",
    )
    usage: LLMUsage = Field(description="Token usage of the model call(s) behind this response.")


# --- Composer: the agentic meal-composition loop --------------------------------


TraceKind = Literal[
    "inspiration", "draft", "check", "search", "options", "reject", "submit", "verify", "judge"
]

# ``draft`` is the model's own prose; it stays in the stored trace and the admin
# views but is filtered out of the public board, where only code-authored steps show.
MODEL_AUTHORED_TRACE_KINDS: frozenset[TraceKind] = frozenset({"draft"})


class TraceEvent(BaseModel):
    """One authored step of the composer's reasoning, for the showcase replay.

    Written for a human watching the agent think, not raw tool JSON: ``text`` is a
    plain-language line and ``kind`` drives the animation's styling. The ``reject``
    events ("parmesan is avoid, dropping it") are the demo payoff. ``compatibility``
    is the stable reading token the frontend maps to a label, set only on the steps
    that read one ingredient.
    """

    kind: TraceKind
    text: str
    ingredient: str | None = None
    compatibility: TraceReading | None = None


def public_trace(events: Iterable[TraceEvent]) -> list[TraceEvent]:
    """The replayable trace with the model's own prose dropped.

    Only code-authored steps reach a public surface: a ``draft`` is the model's text,
    which never makes a safety claim to a visitor. The admin views keep the full trace.
    """
    return [event for event in events if event.kind not in MODEL_AUTHORED_TRACE_KINDS]


class PublicMealView(BaseModel):
    """The full public view of a composed meal, shared by every surface that shows one.

    The daily board card and the curated browse detail are the same shape, so they
    share this base rather than drifting apart. The ``trace`` is filtered to
    code-authored steps (the model's prose never reaches a visitor); ``model`` is
    per-card so attribution stays truthful when a board mixes models. No verdict
    field travels: membership in the approved pool is the safety signal, and the
    client derives its dish badge from ``cautioned_ingredients`` being non-empty.
    """

    meal_type: MealType
    model: str = Field(description="Which model composed this meal.")
    name: str
    description: str
    ingredients: list[ProposedIngredient]
    recipe: list[str] | None
    tags: list[str]
    cautioned_ingredients: list[CautionedIngredient] = Field(
        default_factory=list,
        description="Moderately compatible ingredients kept in the meal, with the "
        "index's moderation note for each.",
    )
    trace: list[TraceEvent]


class PublicMealCard(BaseModel):
    """One approved meal as the browse *list* serves it: a lean summary, not the meal.

    The list ships no ingredients, recipe, or trace, only whether a recipe and a
    replayable trace exist (``has_recipe`` / ``has_trace``), so a page of many meals
    stays small; the full detail loads from ``GET /api/v1/meals/{id}`` on click. The
    ``id`` is the stable key and the deep link to that detail.
    """

    id: UUID
    meal_type: MealType
    model: str
    name: str
    description: str
    tags: list[str]
    has_recipe: bool
    has_trace: bool


class PublicMealDetail(PublicMealView):
    """One approved meal in full: the deep-linked detail with recipe and replay trace."""

    id: UUID


class PublicMealPage(BaseModel):
    """One page of the browse plus the total approved count, so the client can page."""

    items: list[PublicMealCard]
    total: int


class ComposedMealCard(BaseModel):
    """A composed meal without its trace: the public card and the streamed result.

    No per-meal verdict travels here: nothing the index flags as avoid survived (or
    the meal was never returned), so safety is carried by construction plus admin
    approval, not a field. ``unverified_ingredients`` are the ones absent from the
    index, accepted by the automated gate but surfaced so the reviewing admin
    closes that gap with eyes open rather than the gate hiding it.
    ``cautioned_ingredients`` are the moderately compatible ones the composer kept
    within its cap, each carrying the index's moderation note.
    """

    name: str
    meal_type: MealType
    description: str
    ingredients: list[ProposedIngredient]
    recipe: list[str] | None
    tags: list[str]
    unverified_ingredients: list[str] = Field(default_factory=list)
    cautioned_ingredients: list[CautionedIngredient] = Field(default_factory=list)
    model: str
    usage: LLMUsage = Field(
        default_factory=LLMUsage,
        description="Token usage of every model call the composition took.",
    )

    @classmethod
    def from_meal(cls, meal: "ComposedMeal") -> "ComposedMealCard":
        """The card view of a composed meal, dropping the reasoning trace."""
        return cls(**meal.model_dump(exclude={"reasoning_trace"}))


class ComposedMeal(ComposedMealCard):
    """The full composed meal, carrying the reasoning trace persisted by the batch."""

    reasoning_trace: list[TraceEvent]


class TraceStreamItem(BaseModel):
    """One reasoning step on the live composer stream, tagged for the consumer."""

    type: Literal["trace"] = "trace"
    event: TraceEvent


class MealStreamItem(BaseModel):
    """The terminal item on the live stream: the finished meal, without its trace.

    The consumer assembled the trace from the ``trace`` items already, so the meal
    rides without it rather than re-sending every step.
    """

    type: Literal["meal"] = "meal"
    meal: ComposedMealCard

    @classmethod
    def of(cls, meal: ComposedMeal) -> "MealStreamItem":
        return cls(meal=ComposedMealCard.from_meal(meal))


class SavedEvent(BaseModel):
    """The terminal frame on a saving compose stream: the persisted row's id."""

    id: UUID


class SlotStartEvent(BaseModel):
    """Announces the board slot about to compose; the client clears its live log on it."""

    meal_type: MealType
    index: int
    total: int


class BoardSummaryEvent(BaseModel):
    """The terminal frame of a board run: how each of the date's slots ended up.

    ``skipped`` are the slots already holding a pending or approved suggestion, which
    a board run never replaces.
    """

    composed: list[MealType]
    failed: list[MealType]
    skipped: list[MealType]


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


class MealJudgement(BaseModel):
    """The quality judge's structured verdict: five binary criteria, reasons on a no.

    Booleans, not a score the model invents: each criterion is answered on its own
    and code derives the score, so the threshold lives in configuration rather than
    in prompt wording.
    """

    substantial: bool = Field(description="Is the meal substantial enough for its meal type?")
    coherent: bool = Field(
        description="Does it read as one coherent dish rather than a pile of ingredients?"
    )
    flavors_plausible: bool = Field(description="Do the flavours plausibly work together?")
    recipe_uses_ingredients: bool = Field(
        description="Does the recipe use the listed ingredients sensibly?"
    )
    appealing: bool = Field(description="Would a person browsing a food site choose to eat this?")
    reasons: list[str] = Field(
        default_factory=list,
        description="One short reason per criterion answered no; empty when all pass.",
    )

    def score(self) -> int:
        """How many criteria passed, out of five."""
        return sum(self._criteria().values())

    def failed_criteria(self) -> list[str]:
        """The names of the criteria answered no, in declaration order."""
        return [name for name, passed in self._criteria().items() if not passed]

    def _criteria(self) -> dict[str, bool]:
        return {
            "substantial": self.substantial,
            "coherent": self.coherent,
            "flavors_plausible": self.flavors_plausible,
            "recipe_uses_ingredients": self.recipe_uses_ingredients,
            "appealing": self.appealing,
        }


class SubmitMeal(BaseModel):
    """Submit the finished meal once every ingredient is verified index-safe."""

    name: str = Field(description="The dish name, short and appetising.")
    description: str = Field(description="One or two sentences describing the meal.")
    ingredients: list[ProposedIngredientDraft] = Field(
        description="Every ingredient, each with a short food-group and preparation category."
    )
    recipe: list[str] = Field(default_factory=list, description="Ordered preparation steps.")
    tags: list[str] = Field(default_factory=list, description="A few short descriptive tags.")
