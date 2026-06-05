from fastapi import APIRouter, Depends, Query

from app.dependencies import get_ingredient_service
from app.enums import CompatibilityVerdict
from app.schemas.ingredient import IngredientCandidate, IngredientLookupResponse
from app.services.ingredient_service import IngredientMatch, IngredientService, is_ambiguous

router = APIRouter(prefix="/api/v1/histamine", tags=["histamine"])


def _to_candidate(match: IngredientMatch) -> IngredientCandidate:
    row = match.ingredient
    return IngredientCandidate(
        name=row.name,
        match_type=match.match_type,
        score=round(match.score, 3),
        compatibility=CompatibilityVerdict.from_compatibility(row.compatibility),
        mechanisms=list(row.mechanisms),
        category=row.category,
        notes=row.notes,
        sources=list(row.sources),
    )


@router.get("/ingredient", response_model=IngredientLookupResponse)
async def lookup_ingredient(
    name: str = Query(
        min_length=1,
        max_length=IngredientService.max_query_length,
        description="Ingredient name to look up.",
    ),
    service: IngredientService = Depends(get_ingredient_service),
) -> IngredientLookupResponse:
    """Return the curated index's candidate matches for an ingredient name.

    The candidates are reported as-is, ambiguity included; disambiguation is the
    caller's job. A miss returns an empty list, which means unknown, not safe.
    """
    candidates = await service.find_candidates(name)
    return IngredientLookupResponse(
        query=name,
        found=bool(candidates),
        ambiguous=is_ambiguous(candidates),
        candidates=[_to_candidate(match) for match in candidates],
    )
