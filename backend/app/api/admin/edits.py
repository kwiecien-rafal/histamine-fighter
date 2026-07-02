"""Re-verification helpers for the admin edit endpoints (curated and daily).

Both edit routes re-run an edited meal through the same ingredient index check a
composition gets, but the policy at this boundary is deliberately looser than the
composer's: a human is in the loop. A ``depends`` reading (moderately compatible)
never blocks, an ``avoid`` reading blocks once and can then be confirmed past via
``confirm_flagged``, and the recipe prose is not scanned at all so an admin note
like "fine in moderation" is not rejected for naming a flagged term. Only an
unverifiable reading (the lookup itself failed) blocks unconditionally, since
confirming it would be confirming blind.

Two helpers split the work: ``verify_edit`` is pure (DB-touching, no HTTP) and
re-derives the verdict via the shared ``verify_submission``; ``ensure_safe`` is the
HTTP boundary that applies the policy and turns a refusal into a 422.
"""

from fastapi import HTTPException, status

from app.agents.meal_verification import MealVerification
from app.enums import TraceReading
from app.schemas.admin import MealEditFields
from app.services.ingredient_lookup import verify_submission
from app.services.ingredient_service import IngredientService

_EDIT_UNSAFE = "The edit introduces an ingredient the index flags."
_EDIT_UNVERIFIABLE = "Some ingredients could not be checked against the index. Try again."


async def verify_edit(service: IngredientService, payload: MealEditFields) -> MealVerification:
    """Re-derive an edited meal's ingredient verdict against the index."""
    return await verify_submission(service, payload.ingredients)


def ensure_safe(verification: MealVerification, *, confirmed: bool) -> list[str]:
    """Apply the admin gate policy; raise 422 on refusal.

    Returns the formatted flagged items ("name (level)") the admin confirmed past,
    for the caller to record alongside the unverified list, or an empty list when
    nothing was flagged.
    """
    errors = [b for b in verification.blockers if b[1] is TraceReading.UNVERIFIABLE]
    overridable = [b for b in verification.blockers if b[1] is TraceReading.AVOID]
    if errors:
        raise _rejection(_EDIT_UNVERIFIABLE, errors, can_confirm=False)
    if overridable and not confirmed:
        raise _rejection(_EDIT_UNSAFE, overridable, can_confirm=True)
    return _formatted(overridable) if confirmed else []


def _rejection(
    message: str, blockers: list[tuple[str, TraceReading]], *, can_confirm: bool
) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"message": message, "blockers": _formatted(blockers), "can_confirm": can_confirm},
    )


def _formatted(blockers: list[tuple[str, TraceReading]]) -> list[str]:
    return [f"{name} ({reading.value})" for name, reading in blockers]
