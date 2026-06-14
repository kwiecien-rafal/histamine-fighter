"""Retrieval precision/recall eval over the real curated seed file.

Loads the shipped histamine index into the test database and checks
``find_candidates`` against labelled cases harvested from the seed and the real
dish-decomposition logs. It is both how the fuzzy floor and relevance ratio were
tuned and the guard that holds them: a future change that reintroduces a
collision ("salt" pulling in Salami) or drops a typo match fails here, with the
offending query named.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.scripts.seed_histamine_db import SEED_FILE, load_rows, upsert_ingredients
from app.services.ingredient_service import IngredientService

# Loads the full curated seed into Postgres, so it is excluded from the fast tier
# (pyproject addopts) and run as its own CI step against the provisioned DB.
pytestmark = pytest.mark.seed_eval


class _Case:
    """One labelled retrieval expectation.

    ``top`` is the row that must rank first (None means no candidates at all).
    ``absent`` names rows that must not appear, the clinical mismatches and weak
    fuzzy neighbours the floor and relevance ratio exist to drop.
    """

    def __init__(self, query: str, top: str | None, absent: tuple[str, ...] = ()) -> None:
        self.query = query
        self.top = top
        self.absent = absent


_CASES = [
    # Alias and exact hits resolve to the curated row, suppressing fuzzy noise.
    _Case("salt", "Table Salt", absent=("Salami", "Iodized Salt")),
    _Case("black pepper", "Black Pepper"),
    _Case("ground beef", "Minced Meat", absent=("Beef",)),
    _Case("spaghetti", "Pasta"),
    _Case("tomato", "Tomato"),
    _Case("cucumber", "Cucumber"),
    _Case("spinach", "Spinach"),
    # Typos still reach their row past five characters.
    _Case("tomatos", "Tomato", absent=("Tomato Juice",)),
    _Case("chedar", "Cheddar"),
    _Case("cinamon", "Cinnamon"),
    _Case("mozzarela", "Mozzarella"),
    # A query matching nothing stays empty rather than grabbing a stray neighbour.
    _Case("xyzzyqwerty", None),
]


async def _seed(session: AsyncSession) -> IngredientService:
    await upsert_ingredients(session, load_rows(SEED_FILE))
    await session.flush()
    return IngredientService(session)


async def test_retrieval_eval(session: AsyncSession) -> None:
    service = await _seed(session)
    failures: list[str] = []
    for case in _CASES:
        names = [match.ingredient.name for match in await service.find_candidates(case.query)]
        top = names[0] if names else None
        if top != case.top:
            failures.append(f"{case.query!r}: ranked {top!r} first, expected {case.top!r}")
        for name in case.absent:
            if name in names:
                failures.append(f"{case.query!r}: {name!r} should not appear (got {names})")
    assert not failures, "retrieval eval regressions:\n" + "\n".join(failures)
