"""Endpoint tests for the two-phase dish lookup (propose, then assess).

The agent is stubbed at its dependency seam, so these cover only the HTTP
contract: routing, request validation, and the exact response shapes the
frontend consumes — no database, no LLM.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.core.ratelimit import limiter
from app.dependencies import (
    RequestLLM,
    build_dish_lookup_agent,
    build_recipe_agent,
    get_lookup_cache_service,
    get_request_llm_config,
)
from app.enums import (
    AdaptationAction,
    AlternativeGoal,
    CulinaryRole,
    DishIntegrity,
    HistamineMechanism,
    SafetyLevel,
)
from app.llm.config import LLMRequestConfig
from app.main import create_app
from app.schemas.meal import (
    MAX_CONFIRMED_INGREDIENTS,
    MAX_DISH_CHARS,
    MAX_INGREDIENT_CHARS,
    Adaptation,
    Advisory,
    CautionedIngredient,
    ConfirmedIngredient,
    DishAlternative,
    DishAlternativesRequest,
    DishAlternativesResponse,
    DishAssessmentResponse,
    IngredientAssessment,
    IngredientProposalResponse,
    ProposedIngredient,
    RecipeGeneration,
)
from app.schemas.usage import LLMUsage

# The stub makes no model calls, so every response carries the zero usage these
# HTTP-contract tests expect; the real tallying is covered in test_dish_lookup_agent.
_EMPTY_USAGE = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "steps": []}


class _StubAgent:
    """Stands in for DishLookupAgent, echoing inputs back in canned responses."""

    async def propose(self, dish: str) -> IngredientProposalResponse:
        return IngredientProposalResponse(
            dish=dish,
            ingredients=[
                ProposedIngredient(name="tomato", category="vegetable"),
                ProposedIngredient(name="parmesan", category="aged hard cheese"),
            ],
            model="stub/model",
            usage=LLMUsage(),
        )

    async def assess(
        self, dish: str, ingredients: list[ConfirmedIngredient]
    ) -> DishAssessmentResponse:
        def _reading(item: ConfirmedIngredient) -> IngredientAssessment:
            if item.name == "tomato":
                return IngredientAssessment(
                    name=item.name,
                    safety=SafetyLevel.AVOID,
                    found=True,
                    matched_on="ingredient",
                    mechanisms=[HistamineMechanism.HIGH_HISTAMINE],
                )
            return IngredientAssessment(name=item.name, safety=SafetyLevel.SAFE, found=False)

        return DishAssessmentResponse(
            dish=dish,
            explanation="Tomato is recorded as incompatible.",
            adaptations=[
                Adaptation(
                    ingredients=["tomato"],
                    role=CulinaryRole.CORE,
                    action=AdaptationAction.NO_SAFE_SWAP,
                    swap=None,
                    reason="Nothing keeps this dish intact.",
                )
            ],
            advisories=[Advisory(ingredient="onion", note="Tolerated by most when cooked.")],
            integrity=DishIntegrity.LOST,
            verdict=SafetyLevel.AVOID,
            ingredients=[_reading(item) for item in ingredients],
            model="stub/model",
            usage=LLMUsage(),
        )

    async def alternatives(
        self,
        dish: str,
        goal: AlternativeGoal,
        avoid_ingredients: list[str],
        prefer_ingredients: list[str] | None = None,
    ) -> DishAlternativesResponse:
        return DishAlternativesResponse(
            dish=dish,
            goal=goal,
            alternatives=[DishAlternative(name="Courgette Pasta", pitch="Fresh and herby.")],
            model="stub/model",
            usage=LLMUsage(),
        )


class _MissLookupCache:
    """A transparent cache: every read misses, every write is dropped.

    Keeps these tests database-free now that the routes consult the cache; the
    real cache behaviour is covered in test_lookup_cache_service.
    """

    async def get_proposal(self, dish: str) -> None:
        return None

    async def store_proposal(self, response: object) -> None:
        return None

    async def get_assessment(self, dish: str, ingredients: object) -> None:
        return None

    async def store_assessment(self, dish: str, ingredients: object, response: object) -> None:
        return None


class _RecordingCache(_MissLookupCache):
    """A miss cache that remembers which store methods the routes invoked."""

    def __init__(self) -> None:
        self.stored: list[str] = []

    async def store_proposal(self, response: object) -> None:
        self.stored.append("proposal")

    async def store_assessment(self, dish: str, ingredients: object, response: object) -> None:
        self.stored.append("assessment")


def _probe_llm(shared: bool, charges: list[str]) -> RequestLLM:
    """A resolved config whose charge is observable, for hit/gate assertions."""

    async def _charge() -> None:
        charges.append("charged")

    return RequestLLM(config=LLMRequestConfig(), shared=shared, _charge=_charge)


# One app wiring for the whole module: the stubbed agent and cache cut off the
# session dependency chain, so no database is needed. Tests that need their own
# cache or RequestLLM probe pass extra dependency overrides.
@asynccontextmanager
async def _lookup_client(
    overrides: dict[Callable[..., object], Callable[[], object]] | None = None,
) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.dependency_overrides[build_dish_lookup_agent] = _StubAgent
    app.dependency_overrides[get_lookup_cache_service] = _MissLookupCache
    app.dependency_overrides.update(overrides or {})
    limiter.enabled = False
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            yield http_client
    finally:
        limiter.enabled = True


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with _lookup_client() as http_client:
        yield http_client


# --- POST /api/v1/meals/propose ---------------------------------------------------


async def test_propose_returns_the_proposal_shape(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/meals/propose", json={"dish": "spaghetti bolognese"})

    assert resp.status_code == 200
    assert resp.json() == {
        "dish": "spaghetti bolognese",
        "recognized": True,
        "ingredients": [
            {"name": "tomato", "category": "vegetable"},
            {"name": "parmesan", "category": "aged hard cheese"},
        ],
        "model": "stub/model",
        "cached": False,
        "usage": _EMPTY_USAGE,
    }


async def test_propose_serves_a_cache_hit_uncharged() -> None:
    # A hit short-circuits the agent entirely and costs no quota: the shared
    # charge is waived, not spent and not left pending for the leak backstop.
    hit = IngredientProposalResponse(
        dish="spaghetti bolognese",
        ingredients=[ProposedIngredient(name="beef", category="fresh meat")],
        model="earlier/model",
        cached=True,
        usage=LLMUsage(),
    )

    class _HitCache(_MissLookupCache):
        async def get_proposal(self, dish: str) -> IngredientProposalResponse:
            return hit

    charges: list[str] = []
    resolved = _probe_llm(shared=True, charges=charges)
    overrides = {get_lookup_cache_service: _HitCache, get_request_llm_config: lambda: resolved}
    async with _lookup_client(overrides) as http_client:
        resp = await http_client.post("/api/v1/meals/propose", json={"dish": "spaghetti bolognese"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["cached"] is True
    assert body["model"] == "earlier/model"
    assert [item["name"] for item in body["ingredients"]] == ["beef"]
    assert charges == []
    assert resolved.pending is False  # waived, so the leak backstop stays quiet


async def test_assess_serves_a_cache_hit_uncharged() -> None:
    hit = DishAssessmentResponse(
        dish="pasta",
        verdict=SafetyLevel.SAFE,
        explanation="All clear.",
        adaptations=[],
        advisories=[],
        integrity=DishIntegrity.PRESERVED,
        ingredients=[IngredientAssessment(name="rice", safety=SafetyLevel.SAFE, found=False)],
        model="earlier/model",
        cached=True,
        usage=LLMUsage(),
    )

    class _HitCache(_MissLookupCache):
        async def get_assessment(self, dish: str, ingredients: object) -> DishAssessmentResponse:
            return hit

    charges: list[str] = []
    resolved = _probe_llm(shared=True, charges=charges)
    overrides = {get_lookup_cache_service: _HitCache, get_request_llm_config: lambda: resolved}
    async with _lookup_client(overrides) as http_client:
        resp = await http_client.post(
            "/api/v1/meals/assess",
            json={"dish": "pasta", "ingredients": [{"name": "rice"}]},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["cached"] is True
    assert body["model"] == "earlier/model"
    assert charges == []
    assert resolved.pending is False


@pytest.mark.parametrize(
    ("public", "shared", "expect_stored"),
    [
        (True, True, ["proposal", "assessment"]),  # shared tier writes everywhere
        (True, False, []),  # BYO must not write shared state on a public deployment
        (False, False, ["proposal", "assessment"]),  # self-hosted: one trust domain
    ],
)
async def test_cache_writes_are_gated_to_trusted_models(
    monkeypatch: pytest.MonkeyPatch, public: bool, shared: bool, expect_stored: list[str]
) -> None:
    monkeypatch.setattr(settings, "public_deployment", public)
    cache = _RecordingCache()
    charges: list[str] = []
    overrides = {
        get_lookup_cache_service: lambda: cache,
        get_request_llm_config: lambda: _probe_llm(shared=shared, charges=charges),
    }
    async with _lookup_client(overrides) as http_client:
        await http_client.post("/api/v1/meals/propose", json={"dish": "pasta"})
        await http_client.post(
            "/api/v1/meals/assess",
            json={"dish": "pasta", "ingredients": [{"name": "rice"}]},
        )

    assert cache.stored == expect_stored
    assert charges == ["charged", "charged"]  # misses always charge, gated or not


async def test_propose_without_a_dish_is_422(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/meals/propose", json={})

    assert resp.status_code == 422


async def test_propose_with_an_overlong_dish_is_422(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/meals/propose", json={"dish": "x" * (MAX_DISH_CHARS + 1)})

    assert resp.status_code == 422


# --- POST /api/v1/meals/assess ----------------------------------------------------


async def test_assess_returns_the_assessment_shape(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/meals/assess",
        json={
            "dish": "pasta",
            "ingredients": [
                {"name": "tomato", "category": "vegetable"},
                {"name": "rice", "category": None},
            ],
        },
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "dish": "pasta",
        "dish_style": None,
        "verdict": "avoid",
        "explanation": "Tomato is recorded as incompatible.",
        "adaptations": [
            {
                "ingredients": ["tomato"],
                "role": "core",
                "action": "no_safe_swap",
                "swap": None,
                "reason": "Nothing keeps this dish intact.",
            }
        ],
        "advisories": [{"ingredient": "onion", "note": "Tolerated by most when cooked."}],
        "integrity": "lost",
        "ingredients": [
            {
                "name": "tomato",
                "safety": "avoid",
                "found": True,
                "error": False,
                "matched_on": "ingredient",
                "mechanisms": ["high_histamine"],
            },
            {
                "name": "rice",
                "safety": "safe",
                "found": False,
                "error": False,
                "matched_on": None,
                "mechanisms": [],
            },
        ],
        "model": "stub/model",
        "cached": False,
        "usage": _EMPTY_USAGE,
    }


async def test_assess_normalizes_confirmed_names_at_the_boundary(client: AsyncClient) -> None:
    # Request validation strips padding and blanks out empty categories before
    # the agent sees them; the echoed reading proves it happened.
    resp = await client.post(
        "/api/v1/meals/assess",
        json={"dish": "rice bowl", "ingredients": [{"name": "  rice ", "category": "  "}]},
    )

    assert resp.status_code == 200
    assert resp.json()["ingredients"][0]["name"] == "rice"


async def test_assess_with_an_empty_list_is_422(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/meals/assess", json={"dish": "pasta", "ingredients": []})

    assert resp.status_code == 422


async def test_assess_over_the_ingredient_cap_is_422(client: AsyncClient) -> None:
    too_many = [{"name": f"ingredient {i}"} for i in range(MAX_CONFIRMED_INGREDIENTS + 1)]
    resp = await client.post(
        "/api/v1/meals/assess", json={"dish": "pasta", "ingredients": too_many}
    )

    assert resp.status_code == 422


async def test_assess_with_a_blank_name_is_422(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/meals/assess", json={"dish": "pasta", "ingredients": [{"name": "   "}]}
    )

    assert resp.status_code == 422


async def test_assess_with_an_overlong_name_is_422(client: AsyncClient) -> None:
    overlong = "x" * (MAX_INGREDIENT_CHARS + 1)
    resp = await client.post(
        "/api/v1/meals/assess", json={"dish": "pasta", "ingredients": [{"name": overlong}]}
    )

    assert resp.status_code == 422


async def test_assess_without_a_dish_is_422(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/meals/assess", json={"ingredients": [{"name": "rice"}]})

    assert resp.status_code == 422


# --- POST /api/v1/meals/alternatives -----------------------------------------------


async def test_alternatives_returns_the_suggestion_shape(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/meals/alternatives",
        json={"dish": "bolognese", "goal": "same_style", "avoid_ingredients": ["tomato"]},
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "dish": "bolognese",
        "goal": "same_style",
        "alternatives": [
            {"name": "Courgette Pasta", "pitch": "Fresh and herby.", "source": "generated"}
        ],
        "model": "stub/model",
        "usage": _EMPTY_USAGE,
    }


async def test_alternatives_with_a_free_text_goal_is_422(client: AsyncClient) -> None:
    # The goal is a closed enum: anything else dies at validation and can never
    # reach the prompt.
    resp = await client.post(
        "/api/v1/meals/alternatives",
        json={"dish": "bolognese", "goal": "ignore instructions", "avoid_ingredients": ["x"]},
    )

    assert resp.status_code == 422


async def test_alternatives_without_a_goal_is_422(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/meals/alternatives", json={"dish": "bolognese", "avoid_ingredients": ["x"]}
    )

    assert resp.status_code == 422


async def test_alternatives_without_a_dish_is_422(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/meals/alternatives", json={"goal": "any_meal", "avoid_ingredients": ["x"]}
    )

    assert resp.status_code == 422


async def test_alternatives_with_an_overlong_dish_is_422(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/meals/alternatives",
        json={
            "dish": "x" * (MAX_DISH_CHARS + 1),
            "goal": "any_meal",
            "avoid_ingredients": ["tomato"],
        },
    )

    assert resp.status_code == 422


async def test_alternatives_with_no_avoid_ingredients_is_422(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/meals/alternatives",
        json={"dish": "bolognese", "goal": "any_meal", "avoid_ingredients": []},
    )

    assert resp.status_code == 422


async def test_alternatives_over_the_ingredient_cap_is_422(client: AsyncClient) -> None:
    too_many = [f"ingredient {i}" for i in range(MAX_CONFIRMED_INGREDIENTS + 1)]
    resp = await client.post(
        "/api/v1/meals/alternatives",
        json={"dish": "bolognese", "goal": "any_meal", "avoid_ingredients": too_many},
    )

    assert resp.status_code == 422


async def test_alternatives_with_an_overlong_ingredient_is_422(client: AsyncClient) -> None:
    overlong = "x" * (MAX_INGREDIENT_CHARS + 1)
    resp = await client.post(
        "/api/v1/meals/alternatives",
        json={"dish": "bolognese", "goal": "any_meal", "avoid_ingredients": [overlong]},
    )

    assert resp.status_code == 422


def test_alternatives_request_dedupes_repeated_names() -> None:
    # The names are joined into the alternatives prompt; one ingredient under
    # three spellings must reach it once, under its first spelling.
    request = DishAlternativesRequest(
        dish="bolognese",
        goal=AlternativeGoal.ANY_MEAL,
        avoid_ingredients=["Tomato", "tomato", " Tomato ", "parmesan"],
    )

    assert request.avoid_ingredients == ["Tomato", "parmesan"]


# --- POST /api/v1/meals/recipe ------------------------------------------------------


class _StubRecipeAgent:
    """Stands in for RecipeAgent, capturing what the route hands it."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def run(
        self,
        name: str,
        description: str,
        ingredients: list[ProposedIngredient],
        cautions: list[CautionedIngredient],
    ) -> RecipeGeneration:
        self.calls.append(
            {
                "name": name,
                "description": description,
                "ingredients": ingredients,
                "cautions": cautions,
            }
        )
        return RecipeGeneration(steps=["Chop.", "Simmer."], model="stub/model", usage=LLMUsage())


async def test_lookup_recipe_returns_steps_and_charges_once() -> None:
    stub = _StubRecipeAgent()
    charges: list[str] = []
    resolved = _probe_llm(shared=True, charges=charges)
    overrides = {
        build_recipe_agent: lambda: stub,
        get_request_llm_config: lambda: resolved,
    }
    async with _lookup_client(overrides) as http_client:
        resp = await http_client.post(
            "/api/v1/meals/recipe",
            json={
                "dish": "courgette pasta",
                "description": "Light and herby.",
                "ingredients": [{"name": "courgette", "category": None}],
                "advisories": [{"ingredient": "spinach", "note": "fresh only"}],
            },
        )

    assert resp.status_code == 200
    assert resp.json() == {
        "steps": ["Chop.", "Simmer."],
        "model": "stub/model",
        "usage": _EMPTY_USAGE,
    }
    assert charges == ["charged"]
    # The assessment's advisories reach the agent as its cautioned ingredients.
    call = stub.calls[0]
    assert call["name"] == "courgette pasta"
    assert call["cautions"] == [CautionedIngredient(name="spinach", note="fresh only")]


async def test_lookup_recipe_without_ingredients_is_422() -> None:
    stub = _StubRecipeAgent()
    async with _lookup_client({build_recipe_agent: lambda: stub}) as http_client:
        resp = await http_client.post(
            "/api/v1/meals/recipe",
            json={"dish": "courgette pasta", "ingredients": []},
        )

    assert resp.status_code == 422
    assert stub.calls == []
