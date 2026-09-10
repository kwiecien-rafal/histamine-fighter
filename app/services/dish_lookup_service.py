"""Orchestration for the dish-lookup steps, shared by the API and the pages.

The order of each step is a cost contract, not an implementation detail: the cache is
read before the shared-tier allowance is charged, so a hit is free; the charge lands at
the model-call boundary; and a write-back is gated on the tier that produced the answer.
Holding that here rather than in a route is what stops the JSON API and the rendered
pages from drifting apart on any of it.
"""

from app.agents.dish_lookup import DishLookupAgent
from app.agents.recipe import RecipeAgent
from app.config import settings
from app.llm.request import RequestLLM
from app.schemas.meal import (
    AdaptedDish,
    CautionedIngredient,
    DishAlternativesRequest,
    DishAlternativesResponse,
    DishAssessmentRequest,
    DishAssessmentResponse,
    DishLookupRequest,
    DishRewriteRequest,
    IngredientProposalResponse,
    LookupRecipeRequest,
    ProposedIngredient,
    RecipeGeneration,
)
from app.services.lookup_cache_service import LookupCacheService


def _cache_writes_allowed(resolved: RequestLLM) -> bool:
    """Whether this request's output may enter the shared lookup cache."""
    return resolved.shared or not settings.public_deployment


class DishLookupService:
    """The dish-lookup flow: propose, assess, adapt, recipe, alternatives."""

    def __init__(self, cache: LookupCacheService) -> None:
        self._cache = cache

    async def propose(
        self,
        payload: DishLookupRequest,
        *,
        agent: DishLookupAgent,
        resolved: RequestLLM,
    ) -> IngredientProposalResponse:
        """Ask the model what is in the dish, serving a cached proposal when there is one."""
        # The cache is read before charging: a hit costs no model call, so it must
        # not consume shared-tier quota (the rate limit still bounds it).
        cached = await self._cache.get_proposal(payload.dish)
        if cached is not None:
            # Release the unspent shared-tier charge so the leak backstop does not
            # bill a free answer as a forgotten charge.
            resolved.waive()
            return cached
        # The same instance the agent was built from (FastAPI caches the dependency
        # per request); charging here, at the model-call boundary, is the contract.
        await resolved.charge()
        response = await agent.propose(dish=payload.dish)
        if _cache_writes_allowed(resolved):
            await self._cache.store_proposal(response)
        return response

    async def assess(
        self,
        payload: DishAssessmentRequest,
        *,
        agent: DishLookupAgent,
        resolved: RequestLLM,
    ) -> DishAssessmentResponse:
        """Grade the confirmed ingredient list, serving a still-valid cached verdict first."""
        # A hit is only served while its grounding fingerprint still matches the
        # live index — no model call either way; see the service.
        cached = await self._cache.get_assessment(payload.dish, payload.ingredients)
        if cached is not None:
            resolved.waive()
            return cached
        await resolved.charge()
        response = await agent.assess(dish=payload.dish, ingredients=payload.ingredients)
        if _cache_writes_allowed(resolved):
            await self._cache.store_assessment(payload.dish, payload.ingredients, response)
        return response

    async def adapt(
        self,
        payload: DishRewriteRequest,
        *,
        agent: DishLookupAgent,
        resolved: RequestLLM,
    ) -> tuple[DishAssessmentResponse, AdaptedDish]:
        """Assess the dish, then rewrite it into a version the index supports."""
        cached = await self._cache.get_rewrite(payload.dish, payload.ingredients)
        if cached is None:
            await resolved.charge()
        assessment = await self.assess(
            DishAssessmentRequest(dish=payload.dish, ingredients=payload.ingredients),
            agent=agent,
            resolved=resolved,
        )
        if cached is not None:
            # Nothing to rewrite. The assess above already settled the charge either
            # way, so this only releases one it left pending — a hit on both tiers.
            resolved.waive()
            return assessment, cached
        adapted = await agent.adapt(payload.dish, payload.ingredients, assessment)
        if _cache_writes_allowed(resolved):
            await self._cache.store_rewrite(payload.dish, payload.ingredients, adapted)
        return assessment, adapted

    async def recipe(
        self,
        payload: LookupRecipeRequest,
        *,
        agent: RecipeAgent,
        resolved: RequestLLM,
    ) -> RecipeGeneration:
        """Write a recipe for an assessed dish, persisting nothing."""
        await resolved.charge()
        return await agent.run(
            name=payload.dish,
            description=payload.description,
            ingredients=[
                ProposedIngredient(name=item.name, category=item.category)
                for item in payload.ingredients
            ],
            cautions=[
                CautionedIngredient(name=item.ingredient, note=item.note)
                for item in payload.advisories
            ],
        )

    async def alternatives(
        self,
        payload: DishAlternativesRequest,
        *,
        agent: DishLookupAgent,
        resolved: RequestLLM,
    ) -> DishAlternativesResponse:
        """Suggest other dishes for a goal when the assessed one cannot be rescued."""
        await resolved.charge()
        # Both ingredient lists are client-asserted: they only steer the suggestion
        # prompt, and every picked suggestion is fully re-vetted via propose/assess.
        return await agent.alternatives(
            dish=payload.dish,
            goal=payload.goal,
            avoid_ingredients=payload.avoid_ingredients,
            prefer_ingredients=payload.prefer_ingredients,
        )
