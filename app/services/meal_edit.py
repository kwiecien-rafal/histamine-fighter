"""The admin index gate for edited meals: curated rows and daily slots alike.

Both edit paths re-run a submitted meal through the same ingredient index check a
composition gets, but the policy at this boundary is deliberately looser than the
composer's: a human is in the loop. A ``depends`` reading (moderately compatible)
never blocks and is recorded as cautioned with the index's note, uncapped where the
composer caps it, an ``avoid`` reading blocks once and can then be confirmed past via
``confirm_flagged``, and the recipe prose is not scanned at all so an admin note
like "fine in moderation" is not rejected for naming a flagged term. Only an
unverifiable reading (the lookup itself failed) blocks unconditionally, since
confirming it would be confirming blind.

Two helpers split the work: ``verify_edit`` re-derives the verdict via the shared
``verify_submission``; ``ensure_safe`` applies the policy and raises. The refusals are
domain errors, not HTTP ones, so the JSON API maps them to status codes and the admin
pages word them into the form the submission came from.
"""

from app.agents.meal_verification import MealVerification
from app.enums import TraceReading
from app.schemas.admin import MealEditFields
from app.services.ingredient_lookup import verify_submission
from app.services.ingredient_service import IngredientService

_EDIT_UNSAFE = "The edit introduces an ingredient the index flags."
_EDIT_UNVERIFIABLE = "Some ingredients could not be checked against the index. Try again."


class UnsafeMealEdit(Exception):
    """The index gate refused the submission. The API boundary maps this to 422.

    Carries the flagged ingredients and whether the same submission would be accepted
    with the admin's confirmation ticked, since the form has to say both.
    """

    def __init__(self, message: str, blockers: list[str], *, can_confirm: bool) -> None:
        self.message = message
        self.blockers = blockers
        self.can_confirm = can_confirm
        super().__init__(message)


class EditTargetMissing(Exception):
    """The row being edited is gone. The API boundary maps this to 404."""


class EditTargetNotPending(Exception):
    """Only a pending row can be edited. The API boundary maps this to 409."""


async def verify_edit(service: IngredientService, payload: MealEditFields) -> MealVerification:
    """Re-derive an edited meal's ingredient verdict against the index."""
    return await verify_submission(service, payload.ingredients)


def ensure_safe(verification: MealVerification, *, confirmed: bool) -> list[str]:
    """Apply the admin gate policy; raise UnsafeMealEdit on refusal.

    Returns the formatted flagged items ("name (level)") the admin confirmed past,
    for the caller to record alongside the unverified list, or an empty list when
    nothing was flagged.
    """
    errors = [b for b in verification.blockers if b[1] is TraceReading.UNVERIFIABLE]
    overridable = [b for b in verification.blockers if b[1] is TraceReading.AVOID]
    if errors:
        raise UnsafeMealEdit(_EDIT_UNVERIFIABLE, _formatted(errors), can_confirm=False)
    if overridable and not confirmed:
        raise UnsafeMealEdit(_EDIT_UNSAFE, _formatted(overridable), can_confirm=True)
    return _formatted(overridable) if confirmed else []


def _formatted(blockers: list[tuple[str, TraceReading]]) -> list[str]:
    return [f"{name} ({reading.value})" for name, reading in blockers]
