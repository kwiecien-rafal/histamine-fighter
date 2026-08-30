"""Page tests for the dish lookup: the two ways in, the version they produce, and saving.

The agent is stubbed, so nothing here calls a model; the lookup rules themselves —
the cache, the verdict, the grounding — are covered against the JSON API and the
agent in their own suites, and these routes call those very handlers. What is
asserted here is the browser's side: the fields each step posts, the assessment the
result page carries from one step to the next, and what a refusal says out loud.
"""

import html
import re

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.enums import (
    AdaptationAction,
    AlternativeGoal,
    CulinaryRole,
    DishIntegrity,
    RewriteOutcome,
    SafetyLevel,
    SaveSource,
)
from app.llm.errors import LLMInvocationError, LLMRejectedError
from app.models import SavedMeal
from app.models.user import User
from app.schemas.meal import (
    Adaptation,
    AdaptedDish,
    Advisory,
    CautionedIngredient,
    ConfirmedIngredient,
    DishAlternative,
    DishAlternativesResponse,
    DishAssessmentResponse,
    IngredientAssessment,
    IngredientChange,
    IngredientProposalResponse,
    ProposedIngredient,
    RecipeGeneration,
)
from app.schemas.usage import LLMUsage, StepUsage
from app.web import lookup

PROPOSED = [
    ProposedIngredient(name="tomato", category="vegetable"),
    ProposedIngredient(name="parmesan", category="aged hard cheese"),
]


def _usage(step: str, input_tokens: int, output_tokens: int) -> LLMUsage:
    """One step's usage, itemized the way an agent reports it.

    The steps are what the page's usage line is built from — a response carrying
    none reads as cached and is not tallied — so a stub that omitted them would
    never exercise the tally at all.
    """
    total = input_tokens + output_tokens
    return LLMUsage(
        calls=1,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total,
        steps=[
            StepUsage(
                step=step,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total,
                reported=True,
            )
        ],
    )


def _assessment(
    *,
    dish: str = "spaghetti bolognese",
    integrity: DishIntegrity = DishIntegrity.LOST,
    adaptations: list[Adaptation] | None = None,
) -> DishAssessmentResponse:
    """An assessed dish that cannot keep its identity, unless a test says otherwise."""
    return DishAssessmentResponse(
        dish=dish,
        dish_style="hearty tomato pasta dish",
        verdict=SafetyLevel.AVOID,
        explanation="Tomato is recorded as incompatible.",
        adaptations=adaptations
        if adaptations is not None
        else [
            Adaptation(
                ingredients=["tomato"],
                role=CulinaryRole.CORE,
                action=AdaptationAction.NO_SAFE_SWAP,
                swap=None,
                reason="Nothing keeps this dish intact without it.",
            )
        ],
        advisories=[Advisory(ingredient="onion", note="Tolerated by most when cooked.")],
        integrity=integrity,
        ingredients=[
            IngredientAssessment(
                name="tomato", safety=SafetyLevel.AVOID, found=True, matched_on="ingredient"
            ),
            IngredientAssessment(name="basil", safety=SafetyLevel.SAFE, found=True),
        ],
        model="stub/model",
        usage=_usage("synthesize", 900, 100),
    )


def _adapted(
    *,
    outcome: RewriteOutcome = RewriteOutcome.ADAPTED,
    name: str = "Spaghetti with Courgette",
    dish: str = "spaghetti bolognese",
    ingredients: list[str] | None = None,
    changes: list[IngredientChange] | None = None,
    blocked: list[str] | None = None,
    verdict: SafetyLevel = SafetyLevel.SAFE,
    cautioned: list[CautionedIngredient] | None = None,
) -> AdaptedDish:
    """A rewritten dish, adapted unless a test asks for one of the dead ends."""
    return AdaptedDish(
        dish=dish,
        name=name,
        outcome=outcome,
        explanation="Courgette carries the sauce.",
        ingredients=[ProposedIngredient(name=item) for item in ingredients or ["courgette"]]
        if outcome in (RewriteOutcome.ADAPTED, RewriteOutcome.ALREADY_SAFE)
        else [],
        changes=changes
        if changes is not None
        else [
            IngredientChange(
                original="tomato", replacement="courgette", reason="Same body, no histamine."
            )
        ],
        trade_off="You lose the tomato depth.",
        verdict=verdict,
        cautioned_ingredients=cautioned or [],
        blocked_ingredients=blocked or [],
        model="stub/model",
        usage=_usage("adapt", 500, 60),
    )


class _StubLookupAgent:
    """Stands in for DishLookupAgent, answering each step from what a test set up."""

    def __init__(
        self,
        *,
        recognized: bool = True,
        result: DishAssessmentResponse | None = None,
        echo_ingredients: bool = True,
        failing: bool = False,
        failure: Exception | None = None,
        adapted: AdaptedDish | None = None,
    ) -> None:
        self._recognized = recognized
        self._result = result or _assessment()
        self._echo_ingredients = echo_ingredients
        self._failing = failing
        self._failure = failure or LLMInvocationError("the model would not answer")
        self._adapted = adapted
        self.alternative_calls: list[AlternativeGoal] = []
        self.propose_calls: list[str] = []
        self.assessed: list[ConfirmedIngredient] = []
        self.adapted_from: list[ConfirmedIngredient] = []

    async def propose(self, dish: str) -> IngredientProposalResponse:
        if self._failing:
            raise self._failure
        self.propose_calls.append(dish)
        return IngredientProposalResponse(
            dish=dish,
            recognized=self._recognized,
            ingredients=PROPOSED if self._recognized else [],
            model="stub/model",
            usage=_usage("propose", 400, 40),
        )

    async def assess(
        self, dish: str, ingredients: list[ConfirmedIngredient]
    ) -> DishAssessmentResponse:
        if self._failing:
            raise self._failure
        self.assessed = ingredients
        if not self._echo_ingredients:
            return self._result
        return self._result.model_copy(
            update={
                "dish": dish,
                "ingredients": [
                    IngredientAssessment(name=item.name, safety=SafetyLevel.SAFE, found=True)
                    for item in ingredients
                ],
            }
        )

    async def adapt(
        self,
        dish: str,
        ingredients: list[ConfirmedIngredient],
        assessment: DishAssessmentResponse,
    ) -> AdaptedDish:
        if self._failing:
            raise self._failure
        self.adapted_from = ingredients
        return self._adapted or _adapted(dish=assessment.dish)

    async def alternatives(
        self,
        dish: str,
        goal: AlternativeGoal,
        avoid_ingredients: list[str],
        prefer_ingredients: list[str] | None = None,
    ) -> DishAlternativesResponse:
        self.alternative_calls.append(goal)
        return DishAlternativesResponse(
            dish=dish,
            goal=goal,
            alternatives=[DishAlternative(name="Courgette ribbon pasta", pitch="Fresh and herby.")],
            model="stub/model",
            usage=_usage("alternatives", 200, 20),
        )


class _StubRecipeAgent:
    """Stands in for RecipeAgent; raises when a test says the model will not answer."""

    def __init__(self, steps: list[str] | None) -> None:
        self._steps = steps

    async def run(self, **kwargs: object) -> RecipeGeneration:
        if self._steps is None:
            raise LLMInvocationError("the model would not answer")
        return RecipeGeneration(
            steps=self._steps, model="recipe/model", usage=_usage("recipe", 300, 80)
        )


def _stub_agent(monkeypatch: pytest.MonkeyPatch, agent: _StubLookupAgent) -> _StubLookupAgent:
    """Answer every lookup step from the stub.

    Patched on the module rather than through ``dependency_overrides``: the pages
    build their agent inside the handler so an unresolvable provider can be page
    copy rather than the API's JSON error body.
    """
    monkeypatch.setattr(lookup, "build_dish_lookup_agent", lambda *args: agent)
    return agent


@pytest.fixture
def agent(monkeypatch: pytest.MonkeyPatch) -> _StubLookupAgent:
    return _stub_agent(monkeypatch, _StubLookupAgent())


def _carried_state(page: str) -> str:
    """The assessment the result page carries in its hidden field, as a form value."""
    match = re.search(r'name="state" value="([^"]*)"', page)
    assert match is not None, "the result page carries no state field"
    return html.unescape(match.group(1))


async def _named(client: AsyncClient, dish: str = "spaghetti bolognese") -> str:
    """Check a dish by name, and hand back the rendered version page."""
    response = await client.post("/lookup/check", data={"dish": dish})
    assert response.status_code == 200
    return response.text


async def _listed(
    client: AsyncClient,
    dish: str = "spaghetti bolognese",
    ingredients: list[str] | None = None,
    **extra: str,
) -> str:
    """Check a dish from a hand-typed list, and hand back the same page."""
    response = await client.post(
        "/lookup/check",
        data={
            "dish": dish,
            "mode": lookup.MODE_OWN,
            "ingredient": ingredients if ingredients is not None else ["tomato", "basil"],
            **extra,
        },
    )
    assert response.status_code == 200
    return response.text


# --- getting started --------------------------------------------------------------


async def test_the_entry_page_offers_both_ways_in(client: AsyncClient) -> None:
    response = await client.get("/lookup")

    assert response.status_code == 200
    assert 'action="/lookup/check"' in response.text
    # The choice rides in the form's values, so it survives a plain post as well as
    # a boosted one, and the editor is served open for a visitor without the script.
    assert 'name="mode" value=""' in response.text
    assert f'name="mode" value="{lookup.MODE_OWN}"' in response.text
    assert 'name="ingredient"' in response.text


async def test_the_entry_page_opens_on_the_editor_when_asked(client: AsyncClient) -> None:
    response = await client.get(f"/lookup?dish=leftover%20risotto&mode={lookup.MODE_OWN}")

    assert response.status_code == 200
    assert 'value="leftover risotto"' in response.text
    assert "data-ingredients-toggle checked" in response.text


# --- checking a dish by name ------------------------------------------------------


async def test_a_named_dish_goes_straight_to_a_version(
    client: AsyncClient, agent: _StubLookupAgent
) -> None:
    page = await _named(client)

    assert "Spaghetti with Courgette" in page
    # No list was put in front of anyone on the way; the model's own guess is what
    # got assessed and reworked.
    assert [item.name for item in agent.adapted_from] == ["tomato", "parmesan"]


async def test_a_named_dish_reports_every_call_behind_the_page(
    client: AsyncClient, agent: _StubLookupAgent
) -> None:
    """Propose, assess and the rewrite ran behind one post, so one line covers them all."""
    page = await _named(client)

    assert 'data-step="safe"' in page
    assert 'data-input="1800"' in page


async def test_an_unrecognized_dish_is_announced_not_rewritten(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_agent(monkeypatch, _StubLookupAgent(recognized=False))

    response = await client.post("/lookup/check", data={"dish": "asdfghjkl"})

    assert response.status_code == 200
    assert "We couldn't place" in response.text
    # Nothing was rewritten, so no version is claimed for it.
    assert "Spaghetti with Courgette" not in response.text
    # Listing it by hand is the only thing left to try, so that half is open.
    assert "data-ingredients-toggle checked" in response.text


async def test_a_blank_dish_never_reaches_the_model(
    client: AsyncClient, agent: _StubLookupAgent
) -> None:
    response = await client.post("/lookup/check", data={"dish": "   "})

    assert response.status_code == 200
    assert "Name the dish before checking it." in response.text


async def test_a_failed_check_is_said_on_the_page(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_agent(monkeypatch, _StubLookupAgent(failing=True))

    response = await client.post("/lookup/check", data={"dish": "spaghetti bolognese"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "couldn&#39;t finish that step" in response.text
    # Back on the form, with what was typed still in it.
    assert 'value="spaghetti bolognese"' in response.text


async def test_a_refused_model_is_named_on_the_page(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model the key cannot use is a settings problem, not a wait-and-retry one."""
    refusal = LLMRejectedError("The provider would not run 'openai/gpt-5.6-luna'.")
    _stub_agent(monkeypatch, _StubLookupAgent(failing=True, failure=refusal))

    response = await client.post("/lookup/check", data={"dish": "spaghetti bolognese"})

    assert response.status_code == 200
    assert "openai/gpt-5.6-luna" in response.text
    assert "Try again in a moment" not in response.text


async def test_the_shared_tier_without_a_session_explains_itself_on_the_page(
    client: AsyncClient, agent: _StubLookupAgent
) -> None:
    """The API answers this with a 401 JSON body; a page has to say it in words."""
    response = await client.post(
        "/lookup/check",
        data={"dish": "spaghetti bolognese"},
        headers={"X-LLM-Provider": "shared"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Sign in to use the shared free tier" in response.text


# --- checking a list you typed yourself -------------------------------------------


async def test_a_hand_typed_list_costs_no_proposal(
    client: AsyncClient, agent: _StubLookupAgent
) -> None:
    """The step that guesses the ingredients has nothing to do when they are given."""
    page = await _listed(client)

    assert "Spaghetti with Courgette" in page
    assert [item.name for item in agent.adapted_from] == ["tomato", "basil"]
    # Propose never ran, so its 400 input tokens are not in the tally.
    assert 'data-input="1400"' in page


async def test_the_editor_normalizes_what_was_typed_into_it(
    client: AsyncClient, agent: _StubLookupAgent
) -> None:
    """Blank lines and repeats are the visitor's own text; they must not reach the agent."""
    await _listed(client, "tomato salad", ["tomato", "", "  ", "TOMATO", "basil"])

    assert [item.name for item in agent.assessed] == ["tomato", "basil"]


def _carried_categories(page: str) -> str:
    """The category map the editor carries in its hidden field, as a form value."""
    match = re.search(r'name="ingredient_categories" value="([^"]*)"', page)
    assert match is not None, "the editor carries no category field"
    return html.unescape(match.group(1))


async def test_an_untouched_row_keeps_its_index_grounding(
    client: AsyncClient, agent: _StubLookupAgent
) -> None:
    """No category is ever a field on the page, yet an unedited one still reaches the index."""
    editor = await client.post(
        "/lookup/check", data={"dish": "spaghetti bolognese", "mode": lookup.MODE_OWN}
    )

    await _listed(
        client,
        "spaghetti bolognese",
        ["tomato", "parmesan"],
        ingredient_categories=_carried_categories(editor.text),
    )

    assert [(item.name, item.category) for item in agent.assessed] == [
        ("tomato", None),
        ("parmesan", None),
    ]


async def test_a_renamed_row_drops_the_category_it_was_rendered_with(
    client: AsyncClient, agent: _StubLookupAgent
) -> None:
    """A stale descriptor cannot ride along, because an edited name matches nothing."""
    editor = await client.post(
        "/lookup/refine", data={"state": _carried_state(await _named(client))}
    )

    await _listed(
        client,
        "spaghetti bolognese",
        ["courgette", "cheddar"],
        ingredient_categories=_carried_categories(editor.text),
    )

    assert [(item.name, item.category) for item in agent.assessed] == [
        ("courgette", None),
        ("cheddar", None),
    ]


async def test_an_unreadable_category_map_costs_grounding_not_the_check(
    client: AsyncClient, agent: _StubLookupAgent
) -> None:
    """Junk in the hidden field degrades to no categories, never to a refused step.

    The safe direction: a category only ever widens the search toward an umbrella
    row, so losing the map can add caution but never remove it.
    """
    await _listed(
        client, "spaghetti bolognese", ["tomato", "parmesan"], ingredient_categories="}not json{"
    )

    assert [item.category for item in agent.assessed] == [None, None]


async def test_a_hand_typed_list_has_to_clear_the_manual_bar(
    client: AsyncClient, agent: _StubLookupAgent
) -> None:
    page = await _listed(client, "leftovers", ["rice"])

    assert f"List at least {lookup.MANUAL_MIN_INGREDIENTS} ingredients." in page
    # Back on the form with the list intact, not thrown away.
    assert 'value="rice"' in page


async def test_a_list_left_over_from_the_other_half_is_never_read(
    client: AsyncClient, agent: _StubLookupAgent
) -> None:
    """The radio decides which half to read, not whether the editor happens to hold rows."""
    await client.post(
        "/lookup/check", data={"dish": "spaghetti bolognese", "ingredient": ["rice", "beans"]}
    )

    assert [item.name for item in agent.assessed] == ["tomato", "parmesan"]


async def test_a_reading_the_index_could_not_make_is_never_shown_as_safe(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unindexed ingredient is neutral, and a failed lookup keeps its caution."""
    unrated = _assessment()
    _stub_agent(
        monkeypatch,
        _StubLookupAgent(
            echo_ingredients=False,
            result=unrated.model_copy(
                update={
                    "ingredients": [
                        IngredientAssessment(name="samphire", safety=SafetyLevel.SAFE, found=False),
                        IngredientAssessment(
                            name="kimchi", safety=SafetyLevel.DEPENDS, found=False, error=True
                        ),
                    ]
                }
            ),
        ),
    )

    page = await _named(client)

    assert "samphire · no known concern" in page
    assert "kimchi · check failed" in page


# --- the version that comes back --------------------------------------------------


async def test_the_version_shows_what_changed_and_what_it_costs(
    client: AsyncClient, agent: _StubLookupAgent
) -> None:
    page = await _named(client)

    assert "What changed" in page
    assert "courgette" in page
    assert "You lose the tomato depth." in page


async def test_the_original_is_kept_as_the_reason_the_version_looks_this_way(
    client: AsyncClient, agent: _StubLookupAgent
) -> None:
    page = await _named(client)

    # Folded away, but there: the index's reading of the dish that was asked about
    # is what the version is answering.
    assert "Why spaghetti bolognese needed changing" in page
    assert "Tomato is recorded as incompatible." in page
    assert "no safe swap" in page


async def test_an_adapted_dish_is_offered_no_pivot(
    client: AsyncClient, agent: _StubLookupAgent
) -> None:
    page = await _named(client)

    # There is a dish to cook, so suggesting other ones would only be noise.
    assert "Find something else" not in page


async def test_a_dish_that_cannot_be_adapted_offers_close_dishes_unprompted(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _stub_agent(
        monkeypatch,
        _StubLookupAgent(
            adapted=_adapted(outcome=RewriteOutcome.IMPOSSIBLE, blocked=["tomato"], changes=[])
        ),
    )

    page = await _named(client)

    # The dead end cost no rewrite call, so the suggestions are fetched for them
    # rather than leaving the page with nothing to do next.
    assert stub.alternative_calls == [AlternativeGoal.SAME_STYLE]
    assert "Courgette ribbon pasta" in page
    assert "tomato" in page


async def test_an_exhausted_run_offers_a_retry_and_claims_nothing(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _stub_agent(
        monkeypatch,
        _StubLookupAgent(adapted=_adapted(outcome=RewriteOutcome.EXHAUSTED, changes=[])),
    )

    page = await _named(client)

    # Running out of attempts is not the same claim as the dish being impossible,
    # so the page says so and spends nothing more on it.
    assert "Try again" in page
    assert "There is no version of this dish" not in page
    assert stub.alternative_calls == []


async def test_an_already_safe_dish_invents_no_changes(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_agent(
        monkeypatch,
        _StubLookupAgent(
            adapted=_adapted(
                outcome=RewriteOutcome.ALREADY_SAFE,
                name="Spaghetti Bolognese",
                ingredients=["tomato", "basil"],
                changes=[],
            )
        ),
    )

    page = await _named(client)

    assert "Nothing to change" in page
    assert "What changed" not in page
    # Nothing was wrong with it, so there is no fold explaining what was.
    assert "needed changing" not in page


# --- retrying a run that came up short --------------------------------------------


async def test_a_retry_reworks_the_same_confirmed_list(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _stub_agent(
        monkeypatch,
        _StubLookupAgent(adapted=_adapted(outcome=RewriteOutcome.EXHAUSTED, changes=[])),
    )
    state = _carried_state(await _named(client))

    response = await client.post("/lookup/adapt", data={"state": state})

    assert response.status_code == 200
    # The list the assessment was computed over, not a fresh guess at the dish.
    assert [item.name for item in stub.adapted_from] == ["tomato", "parmesan"]
    assert stub.propose_calls == ["spaghetti bolognese"]


class _FailsOnRetry(_StubLookupAgent):
    """Rewrites once, then refuses — the exhausted page's Try again pressed in vain."""

    async def adapt(
        self,
        dish: str,
        ingredients: list[ConfirmedIngredient],
        assessment: DishAssessmentResponse,
    ) -> AdaptedDish:
        if self.adapted_from:
            raise LLMInvocationError("the model would not answer")
        return await super().adapt(dish, ingredients, assessment)


async def test_a_failed_retry_stays_on_the_page_it_was_pressed_from(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_agent(
        monkeypatch,
        _FailsOnRetry(adapted=_adapted(outcome=RewriteOutcome.EXHAUSTED, changes=[])),
    )
    state = _carried_state(await _named(client))

    response = await client.post("/lookup/adapt", data={"state": state})

    assert response.status_code == 200
    assert "couldn&#39;t finish that step" in response.text
    # Still the version page, with its retry still on it.
    assert "Try again" in response.text


# --- editing the version and checking it again ------------------------------------


async def test_refining_reopens_the_version_in_the_entry_editor(
    client: AsyncClient, agent: _StubLookupAgent
) -> None:
    state = _carried_state(await _named(client))

    response = await client.post("/lookup/refine", data={"state": state})

    assert response.status_code == 200
    assert 'value="courgette"' in response.text
    # The same form a visitor typing their own list uses, opened on its editor.
    assert 'action="/lookup/check"' in response.text
    assert "data-ingredients-toggle checked" in response.text


async def test_an_edited_version_keeps_the_grounding_of_rows_left_alone(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No category is ever a field on the page, yet an unedited row still reaches the index."""
    stub = _stub_agent(
        monkeypatch,
        _StubLookupAgent(
            adapted=_adapted().model_copy(
                update={
                    "ingredients": [
                        ProposedIngredient(name="courgette", category="vegetable"),
                        ProposedIngredient(name="basil", category="fresh herb"),
                    ]
                }
            )
        ),
    )
    state = _carried_state(await _named(client))
    editor = await client.post("/lookup/refine", data={"state": state})

    await _listed(
        client,
        "Spaghetti with Courgette",
        ["courgette", "cheddar"],
        ingredient_categories=_carried_categories(editor.text),
    )

    # The untouched row keeps its descriptor; the renamed one can carry none, which
    # is the whole guard against a stale category riding along.
    assert [(item.name, item.category) for item in stub.assessed] == [
        ("courgette", "vegetable"),
        ("cheddar", None),
    ]


# --- the pivot to other dishes ----------------------------------------------------


@pytest.fixture
def dead_end(monkeypatch: pytest.MonkeyPatch) -> _StubLookupAgent:
    """A run that came up short, which is when the pivot panel is offered."""
    return _stub_agent(
        monkeypatch,
        _StubLookupAgent(adapted=_adapted(outcome=RewriteOutcome.EXHAUSTED, changes=[])),
    )


async def test_choosing_a_goal_renders_suggestions(
    client: AsyncClient, dead_end: _StubLookupAgent
) -> None:
    state = _carried_state(await _named(client))

    response = await client.post(
        "/lookup/alternatives",
        data={"state": state, "goal": AlternativeGoal.SAME_STYLE.value},
    )

    assert response.status_code == 200
    assert "Courgette ribbon pasta" in response.text
    # Picking one re-enters the flow, so it is checked from scratch like any dish.
    assert 'action="/lookup/check"' in response.text


async def test_a_goal_already_fetched_costs_no_second_call(
    client: AsyncClient, dead_end: _StubLookupAgent
) -> None:
    state = _carried_state(await _named(client))
    first = await client.post(
        "/lookup/alternatives",
        data={"state": state, "goal": AlternativeGoal.SAME_STYLE.value},
    )

    await client.post(
        "/lookup/alternatives",
        data={
            "state": _carried_state(first.text),
            "goal": AlternativeGoal.SAME_STYLE.value,
        },
    )

    assert dead_end.alternative_calls == [AlternativeGoal.SAME_STYLE]


# --- the recipe -------------------------------------------------------------------


async def test_the_recipe_is_written_for_the_version_not_the_original(
    client: AsyncClient, agent: _StubLookupAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    recipe = _StubRecipeAgent(["Slice the courgette.", "Toss."])
    monkeypatch.setattr(lookup, "build_recipe_agent", lambda *args: recipe)
    state = _carried_state(await _named(client))

    response = await client.post("/lookup/recipe", data={"state": state})

    assert response.status_code == 200
    assert "Slice the courgette." in response.text
    assert "recipe/model" in response.text
    assert "Spaghetti with Courgette" in response.text


async def test_a_failed_recipe_leaves_the_version_standing(
    client: AsyncClient, agent: _StubLookupAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lookup, "build_recipe_agent", lambda *args: _StubRecipeAgent(None))
    state = _carried_state(await _named(client))

    response = await client.post("/lookup/recipe", data={"state": state})

    assert response.status_code == 200
    assert "couldn&#39;t finish that step" in response.text
    assert "Courgette carries the sauce." in response.text


async def test_a_dead_end_has_nothing_to_save_or_cook(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_agent(
        monkeypatch,
        _StubLookupAgent(
            adapted=_adapted(outcome=RewriteOutcome.IMPOSSIBLE, blocked=["tomato"], changes=[])
        ),
    )

    page = await _named(client)

    assert 'action="/lookup/recipe"' not in page
    assert 'action="/lookup/save"' not in page


# --- saving -----------------------------------------------------------------------


async def test_an_anonymous_visitor_is_offered_the_account_not_the_save(
    client: AsyncClient, agent: _StubLookupAgent
) -> None:
    page = await _named(client)

    assert "Sign in to save this" in page
    assert 'action="/lookup/save"' not in page


async def test_saving_stores_the_version_and_opens_the_copy(
    user_client: AsyncClient,
    public_user: User,
    session: AsyncSession,
    agent: _StubLookupAgent,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        lookup, "build_recipe_agent", lambda *args: _StubRecipeAgent(["Boil.", "Toss."])
    )
    with_recipe = await user_client.post(
        "/lookup/recipe", data={"state": _carried_state(await _named(user_client))}
    )

    response = await user_client.post(
        "/lookup/save", data={"state": _carried_state(with_recipe.text)}
    )

    assert response.status_code == 303
    saved = (
        await session.execute(select(SavedMeal).where(SavedMeal.user_id == public_user.id))
    ).scalar_one()
    assert response.headers["location"] == f"/profile/meals/{saved.id}"
    assert saved.source is SaveSource.LOOKUP
    # Its own name, its own list, its own verdict — that is what will be cooked.
    assert saved.name == "Spaghetti with Courgette"
    assert saved.verdict is SafetyLevel.SAFE
    assert [item["name"] for item in saved.ingredients] == ["courgette"]
    # The recipe written on the result card rides into the save with it.
    assert saved.recipe == ["Boil.", "Toss."]


async def test_saving_the_same_result_twice_keeps_one_copy(
    user_client: AsyncClient,
    public_user: User,
    session: AsyncSession,
    agent: _StubLookupAgent,
) -> None:
    state = _carried_state(await _named(user_client))

    await user_client.post("/lookup/save", data={"state": state})
    await user_client.post("/lookup/save", data={"state": state})

    rows = (
        (await session.execute(select(SavedMeal).where(SavedMeal.user_id == public_user.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 1


# --- the carried state ------------------------------------------------------------


async def test_an_unreadable_state_starts_the_flow_again(client: AsyncClient) -> None:
    response = await client.post("/lookup/recipe", data={"state": "not json"})

    assert response.status_code == 303
    assert response.headers["location"] == "/lookup"


# --- the AI panel -----------------------------------------------------------------


async def test_every_page_carries_the_ai_panel(client: AsyncClient) -> None:
    response = await client.get("/")

    assert 'id="ai-settings"' in response.text
    assert 'id="ai-usage"' in response.text
    assert 'value="openrouter"' in response.text


async def test_a_public_deployment_offers_no_local_ollama(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "public_deployment", True)

    response = await client.get("/lookup")

    assert 'value="ollama" disabled' in response.text
    assert 'data-public-deployment="true"' in response.text


async def test_the_shared_tier_is_closed_to_an_anonymous_visitor(client: AsyncClient) -> None:
    response = await client.get("/lookup")

    assert 'value="shared" disabled' in response.text
    assert "Sign in</a> to use it." in response.text


async def test_the_shared_tier_opens_once_signed_in(user_client: AsyncClient) -> None:
    response = await user_client.get("/lookup")

    assert 'value="shared" disabled' not in response.text
