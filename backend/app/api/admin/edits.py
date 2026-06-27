"""Re-verification helpers for the admin edit endpoints (curated and daily).

Both edit routes re-run an edited meal through the same index check a composition gets,
so an edit can never be vetted more loosely than the composer (the safety invariant).
Two helpers split the work: ``verify_edit`` is pure (DB-touching, no HTTP) and re-derives
the verdict via the shared ``verify_submission``; ``ensure_safe`` is the HTTP boundary
that turns an unsafe result into a 422 carrying the offending ingredients and recipe
terms for the admin to fix.
"""

from fastapi import HTTPException, status

from app.agents.meal_verification import MealVerification
from app.core.term_match import TermMatcher
from app.schemas.admin import MealEditFields
from app.services.ingredient_lookup import verify_submission
from app.services.ingredient_service import IngredientService

_EDIT_UNSAFE = "The edit introduces an ingredient or recipe step the index flags."


async def verify_edit(service: IngredientService, payload: MealEditFields) -> MealVerification:
    """Re-derive an edited meal's verdict, loading the index's risky terms once."""
    risky_terms = TermMatcher.from_terms(await service.risky_terms())
    return await verify_submission(
        service, payload.ingredients, payload.recipe, risky_terms=risky_terms
    )


def ensure_safe(verification: MealVerification) -> None:
    """Raise 422 with the offending items when an edit fails the index check."""
    if not verification.is_safe:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": _EDIT_UNSAFE, **verification.offending_items()},
        )
