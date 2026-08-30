"""Page tests for the dish lookup: propose, confirm, assess, pivot, recipe, save.

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
    SafetyLevel,
    SaveSource,
)
from app.llm.errors import LLMInvocationError, LLMRejectedError
from app.models import SavedMeal
from app.models.user import User
from app.schemas.meal import (
    Adaptation,
    Advisory,
    ConfirmedIngredient,
    DishAlternative,
    DishAlternativesResponse,
    DishAssessmentResponse,
    IngredientAssessment,
    IngredientProposalResponse,
    ProposedIngredient,
    RecipeGeneration,
)
from app.schemas.usage import LLMUsage
from app.web import lookup

PROPOSED = [
    ProposedIngredient(name="tomato", category="vegetable"),
    ProposedIngredient(name="parmesan", category="aged hard cheese"),
]


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
        usage=LLMUsage(calls=2, input_tokens=900, output_tokens=100, total_tokens=1000),
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
    ) -> None:
        self._recognized = recognized
        self._result = result or _assessment()
        self._echo_ingredients = echo_ingredients
        self._failing = failing
        self._failure = failure or LLMInvocationError("the model would not answer")
        self.alternative_calls: list[AlternativeGoal] = []
        self.assessed: list[ConfirmedIngredient] = []

    async def propose(self, dish: str) -> IngredientProposalResponse:
        if self._failing:
            raise self._failure
        return IngredientProposalResponse(
            dish=dish,
            recognized=self._recognized,
            ingredients=PROPOSED if self._recognized else [],
            model="stub/model",
            usage=LLMUsage(calls=1, input_tokens=400, output_tokens=40, total_tokens=440),
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
            usage=LLMUsage(calls=1),
        )


class _StubRecipeAgent:
    """Stands in for RecipeAgent; raises when a test says the model will not answer."""

    def __init__(self, steps: list[str] | None) -> None:
        self._steps = steps

    async def run(self, **kwargs: object) -> RecipeGeneration:
        if self._steps is None:
            raise LLMInvocationError("the model would not answer")
        return RecipeGeneration(steps=self._steps, model="recipe/model", usage=LLMUsage(calls=1))


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


async def _assessed(client: AsyncClient, dish: str = "spaghetti bolognese") -> str:
    """Drive propose and assess, and hand back the rendered result page."""
    editor = await client.post("/lookup/propose", data={"dish": dish})
    assert editor.status_code == 200
    result = await client.post(
        "/lookup/assess",
        data={"dish": dish, "ingredient": ["tomato", "basil"], "model": "stub/model"},
    )
    assert result.status_code == 200
    return result.text


# --- getting started --------------------------------------------------------------


async def test_the_entry_page_offers_both_ways_in(client: AsyncClient) -> None:
    response = await client.get("/lookup")

    assert response.status_code == 200
    assert 'action="/lookup/propose"' in response.text
    assert 'href="/lookup/manual"' in response.text


async def test_manual_entry_opens_an_empty_editor_with_the_dish_kept(
    client: AsyncClient,
) -> None:
    response = await client.get("/lookup/manual?dish=leftover%20risotto")

    assert response.status_code == 200
    assert 'value="leftover risotto"' in response.text
    # No proposing model, so the dish stays editable and the list starts blank.
    assert 'name="model" value=""' in response.text


# --- proposing --------------------------------------------------------------------


async def test_propose_hands_the_ingredients_to_the_editor(
    client: AsyncClient, agent: _StubLookupAgent
) -> None:
    response = await client.post("/lookup/propose", data={"dish": "spaghetti bolognese"})

    assert response.status_code == 200
    assert 'value="tomato"' in response.text
    assert 'value="parmesan"' in response.text
    # The categories ride in the hidden field, never as something to read or edit.
    assert "aged hard cheese" not in response.text.split('name="ingredient_categories"')[0]
    assert 'name="model" value="stub/model"' in response.text


async def test_propose_reports_the_call_for_the_usage_tally(
    client: AsyncClient, agent: _StubLookupAgent
) -> None:
    response = await client.post("/lookup/propose", data={"dish": "spaghetti bolognese"})

    assert 'data-step="propose"' in response.text
    assert 'data-input="400"' in response.text


async def test_a_cached_proposal_is_flagged_and_costs_nothing(
    client: AsyncClient, agent: _StubLookupAgent
) -> None:
    """A second look at the same dish is served from the cache, so it is not tallied."""
    await client.post("/lookup/propose", data={"dish": "courgette ribbons"})

    response = await client.post("/lookup/propose", data={"dish": "courgette ribbons"})

    assert "shown from an earlier check" in response.text
    assert 'id="llm-call"' not in response.text


async def test_an_unrecognized_dish_is_announced_not_edited(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_agent(monkeypatch, _StubLookupAgent(recognized=False))

    response = await client.post("/lookup/propose", data={"dish": "asdfghjkl"})

    assert response.status_code == 200
    assert "We couldn't place" in response.text
    assert "/lookup/manual?dish=asdfghjkl" in response.text
    # The editor would dead-end on an empty list, so it is never reached.
    assert 'action="/lookup/assess"' not in response.text


async def test_a_blank_dish_never_reaches_the_model(
    client: AsyncClient, agent: _StubLookupAgent
) -> None:
    response = await client.post("/lookup/propose", data={"dish": "   "})

    assert response.status_code == 200
    assert "Name a dish to check." in response.text


async def test_a_failed_proposal_is_said_on_the_page(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_agent(monkeypatch, _StubLookupAgent(failing=True))

    response = await client.post("/lookup/propose", data={"dish": "spaghetti bolognese"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "couldn&#39;t finish that step" in response.text


async def test_a_refused_model_is_named_on_the_page(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model the key cannot use is a settings problem, not a wait-and-retry one."""
    refusal = LLMRejectedError("The provider would not run 'openai/gpt-5.6-luna'.")
    _stub_agent(monkeypatch, _StubLookupAgent(failing=True, failure=refusal))

    response = await client.post("/lookup/propose", data={"dish": "spaghetti bolognese"})

    assert response.status_code == 200
    assert "openai/gpt-5.6-luna" in response.text
    assert "Try again in a moment" not in response.text


async def test_the_shared_tier_without_a_session_explains_itself_on_the_page(
    client: AsyncClient, agent: _StubLookupAgent
) -> None:
    """The API answers this with a 401 JSON body; a page has to say it in words."""
    response = await client.post(
        "/lookup/propose",
        data={"dish": "spaghetti bolognese"},
        headers={"X-LLM-Provider": "shared"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Sign in to use the shared free tier" in response.text


# --- confirming and assessing -----------------------------------------------------


async def test_assess_renders_the_verdict_and_every_reading(
    client: AsyncClient, agent: _StubLookupAgent
) -> None:
    page = await _assessed(client)

    assert "Avoid" in page
    assert "Tomato is recorded as incompatible." in page
    assert "tomato" in page and "basil" in page
    assert "Tolerated by most when cooked." in page
    assert "no safe swap" in page


async def test_the_editor_normalizes_what_was_typed_into_it(
    client: AsyncClient, agent: _StubLookupAgent
) -> None:
    """Blank lines and repeats are the visitor's own text; they must not reach the agent."""
    response = await client.post(
        "/lookup/assess",
        data={
            "dish": "tomato salad",
            "ingredient": ["tomato", "", "  ", "TOMATO", "basil"],
            "model": "stub/model",
        },
    )

    assert response.status_code == 200
    # The stub echoes the confirmed list back as its readings, so the page shows it.
    assert response.text.count("basil") >= 1
    assert "TOMATO" not in response.text


def _carried_categories(page: str) -> str:
    """The category map the editor carries in its hidden field, as a form value."""
    match = re.search(r'name="ingredient_categories" value="([^"]*)"', page)
    assert match is not None, "the editor carries no category field"
    return html.unescape(match.group(1))


async def test_an_untouched_row_keeps_its_index_grounding(
    client: AsyncClient, agent: _StubLookupAgent
) -> None:
    """No category is ever a field on the page, yet an unedited one still reaches the index."""
    editor = await client.post("/lookup/propose", data={"dish": "spaghetti bolognese"})

    response = await client.post(
        "/lookup/assess",
        data={
            "dish": "spaghetti bolognese",
            "ingredient": ["tomato", "parmesan"],
            "ingredient_categories": _carried_categories(editor.text),
            "model": "stub/model",
        },
    )

    assert response.status_code == 200
    assert [(item.name, item.category) for item in agent.assessed] == [
        ("tomato", "vegetable"),
        ("parmesan", "aged hard cheese"),
    ]


async def test_a_renamed_row_drops_the_category_it_was_rendered_with(
    client: AsyncClient, agent: _StubLookupAgent
) -> None:
    """A stale descriptor cannot ride along, because an edited name matches nothing."""
    editor = await client.post("/lookup/propose", data={"dish": "spaghetti bolognese"})

    response = await client.post(
        "/lookup/assess",
        data={
            "dish": "spaghetti bolognese",
            "ingredient": ["tomato", "cheddar"],
            "ingredient_categories": _carried_categories(editor.text),
            "model": "stub/model",
        },
    )

    assert response.status_code == 200
    assert [(item.name, item.category) for item in agent.assessed] == [
        ("tomato", "vegetable"),
        ("cheddar", None),
    ]


async def test_an_unreadable_category_map_costs_grounding_not_the_check(
    client: AsyncClient, agent: _StubLookupAgent
) -> None:
    """Junk in the hidden field degrades to no categories, never to a refused step.

    The safe direction: a category only ever widens the search toward an umbrella
    row, so losing the map can add caution but never remove it.
    """
    response = await client.post(
        "/lookup/assess",
        data={
            "dish": "spaghetti bolognese",
            "ingredient": ["tomato", "parmesan"],
            "ingredient_categories": "}not json{",
            "model": "stub/model",
        },
    )

    assert response.status_code == 200
    assert [item.category for item in agent.assessed] == [None, None]


async def test_a_hand_typed_list_has_to_clear_the_manual_bar(
    client: AsyncClient, agent: _StubLookupAgent
) -> None:
    response = await client.post(
        "/lookup/assess", data={"dish": "leftovers", "ingredient": ["rice"], "model": ""}
    )

    assert response.status_code == 200
    assert f"List at least {lookup.MANUAL_MIN_INGREDIENTS} ingredients." in response.text
    # Back on the editor with the list intact, not thrown away.
    assert "rice" in response.text


async def test_assessing_without_a_dish_name_stays_on_the_editor(
    client: AsyncClient, agent: _StubLookupAgent
) -> None:
    response = await client.post(
        "/lookup/assess", data={"dish": " ", "ingredient": ["rice", "beans"], "model": ""}
    )

    assert response.status_code == 200
    assert "Name the dish before checking it." in response.text


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

    page = await _assessed(client)

    assert "samphire · no known concern" in page
    assert "kimchi · check failed" in page


# --- the pivot to other dishes ----------------------------------------------------


async def test_a_dish_that_survives_its_adaptations_is_offered_no_pivot(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_agent(
        monkeypatch,
        _StubLookupAgent(result=_assessment(integrity=DishIntegrity.PRESERVED, adaptations=[])),
    )

    page = await _assessed(client)

    assert "Find something else" not in page


async def test_choosing_a_goal_renders_suggestions(
    client: AsyncClient, agent: _StubLookupAgent
) -> None:
    state = _carried_state(await _assessed(client))

    response = await client.post(
        "/lookup/alternatives",
        data={"state": state, "goal": AlternativeGoal.SAME_STYLE.value},
    )

    assert response.status_code == 200
    assert "Courgette ribbon pasta" in response.text
    # Picking one re-enters the flow, so it is checked from scratch like any dish.
    assert 'action="/lookup/propose"' in response.text


async def test_a_goal_already_fetched_costs_no_second_call(
    client: AsyncClient, agent: _StubLookupAgent
) -> None:
    state = _carried_state(await _assessed(client))
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

    assert agent.alternative_calls == [AlternativeGoal.SAME_STYLE]


# --- the recipe -------------------------------------------------------------------


async def test_writing_a_recipe_renders_its_steps(
    client: AsyncClient, agent: _StubLookupAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        lookup, "build_recipe_agent", lambda *args: _StubRecipeAgent(["Boil.", "Toss."])
    )
    state = _carried_state(await _assessed(client))

    response = await client.post("/lookup/recipe", data={"state": state})

    assert response.status_code == 200
    assert "Boil." in response.text
    assert "recipe/model" in response.text


async def test_a_failed_recipe_leaves_the_verdict_standing(
    client: AsyncClient, agent: _StubLookupAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lookup, "build_recipe_agent", lambda *args: _StubRecipeAgent(None))
    state = _carried_state(await _assessed(client))

    response = await client.post("/lookup/recipe", data={"state": state})

    assert response.status_code == 200
    assert "couldn&#39;t finish that step" in response.text
    assert "Tomato is recorded as incompatible." in response.text


# --- saving -----------------------------------------------------------------------


async def test_an_anonymous_visitor_is_offered_the_account_not_the_save(
    client: AsyncClient, agent: _StubLookupAgent
) -> None:
    page = await _assessed(client)

    assert "Sign in to save this" in page
    assert 'action="/lookup/save"' not in page


async def test_saving_stores_the_dish_and_opens_the_copy(
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
        "/lookup/recipe", data={"state": _carried_state(await _assessed(user_client))}
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
    assert saved.verdict is SafetyLevel.AVOID
    # The recipe written on the result card rides into the save with it.
    assert saved.recipe == ["Boil.", "Toss."]


async def test_saving_the_same_result_twice_keeps_one_copy(
    user_client: AsyncClient,
    public_user: User,
    session: AsyncSession,
    agent: _StubLookupAgent,
) -> None:
    state = _carried_state(await _assessed(user_client))

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
