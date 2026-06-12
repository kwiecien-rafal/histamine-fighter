"""Endpoint tests for the two-phase dish lookup (propose, then assess).

The agent is stubbed at its dependency seam, so these cover only the HTTP
contract: routing, request validation, and the exact response shapes the
frontend consumes — no database, no LLM.
"""

from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.ratelimit import limiter
from app.dependencies import build_dish_lookup_agent
from app.enums import HistamineMechanism, SafetyLevel
from app.main import create_app
from app.schemas.meal import (
    MAX_CONFIRMED_INGREDIENTS,
    MAX_INGREDIENT_CHARS,
    ConfirmedIngredient,
    DishAssessmentResponse,
    IngredientAssessment,
    IngredientProposalResponse,
    ProposedIngredient,
    Replacement,
)


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
            replacements=[
                Replacement(ingredient="tomato", swap="courgette", reason="low histamine")
            ],
            verdict=SafetyLevel.AVOID,
            ingredients=[_reading(item) for item in ingredients],
            model="stub/model",
        )


# A module-local client: unlike the conftest one, it needs no database — the
# stubbed agent cuts off the whole session dependency chain.
@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.dependency_overrides[build_dish_lookup_agent] = _StubAgent
    limiter.enabled = False
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            yield http_client
    finally:
        limiter.enabled = True


# --- POST /api/v1/meals/propose ---------------------------------------------------


async def test_propose_returns_the_proposal_shape(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/meals/propose", json={"dish": "spaghetti bolognese"})

    assert resp.status_code == 200
    assert resp.json() == {
        "dish": "spaghetti bolognese",
        "ingredients": [
            {"name": "tomato", "category": "vegetable"},
            {"name": "parmesan", "category": "aged hard cheese"},
        ],
        "model": "stub/model",
    }


async def test_propose_without_a_dish_is_422(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/meals/propose", json={})

    assert resp.status_code == 422


async def test_propose_with_an_overlong_dish_is_422(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/meals/propose", json={"dish": "x" * 201})

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
        "verdict": "avoid",
        "explanation": "Tomato is recorded as incompatible.",
        "replacements": [{"ingredient": "tomato", "swap": "courgette", "reason": "low histamine"}],
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
