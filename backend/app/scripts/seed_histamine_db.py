"""Seed the histamine_ingredients table from the curated JSON file.

Schema lives in Alembic migrations; this loads the *data*. It is idempotent:
rows are matched on their normalized name and upserted, so running it again
after editing the JSON updates existing rows and inserts new ones without
creating duplicates. Safe to run in dev setup and CI.

Run it (with the database up and migrations applied):

    uv run --directory backend python -m app.scripts.seed_histamine_db
"""

import asyncio
import json
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.logging import configure_logging
from app.core.normalization import normalize_ingredient_name
from app.db.engine import SessionLocal
from app.enums import Compatibility, HistamineMechanism
from app.models import HistamineIngredient

log = structlog.get_logger()

SEED_FILE = Path(__file__).resolve().parents[2] / "seed_data" / "histamine_ingredients.json"


class IngredientSeedRow(BaseModel):
    """One row of the curated seed file.

    The normalized lookup key is derived from ``name`` at load time, so it is
    not stored in the file. ``extra="forbid"`` turns a mistyped field name into
    a loud validation error rather than a silently dropped value.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    compatibility: Compatibility | None = None
    mechanisms: list[HistamineMechanism] = Field(default_factory=list)
    category: str | None = None
    aliases: list[str] = Field(default_factory=list)
    notes: str | None = None
    sources: list[str] = Field(min_length=1)


def load_rows(path: Path) -> list[IngredientSeedRow]:
    """Read and validate the seed file, failing loudly on bad data."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = TypeAdapter(list[IngredientSeedRow]).validate_python(raw)

    keys = [normalize_ingredient_name(row.name) for row in rows]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise ValueError(f"Duplicate ingredient names in {path.name}: {', '.join(duplicates)}")
    return rows


def _to_values(rows: list[IngredientSeedRow]) -> list[dict[str, Any]]:
    """Map validated rows to insert parameters, deriving the normalized key."""
    return [
        {
            "name": row.name,
            "normalized_name": normalize_ingredient_name(row.name),
            "compatibility": row.compatibility,
            "mechanisms": list(row.mechanisms),
            "category": row.category,
            "aliases": list(row.aliases),
            "notes": row.notes,
            "sources": list(row.sources),
        }
        for row in rows
    ]


async def seed() -> None:
    """Upsert every row from the seed file and log inserted/updated counts."""
    rows = load_rows(SEED_FILE)
    if not rows:
        log.warning("seed.empty", file=str(SEED_FILE))
        return

    insert_stmt = pg_insert(HistamineIngredient).values(_to_values(rows))
    excluded = insert_stmt.excluded
    # xmax is 0 for a freshly inserted row and non-zero for one updated by the
    # conflict clause, which lets a single statement report both counts.
    upsert = insert_stmt.on_conflict_do_update(
        index_elements=["normalized_name"],
        set_={
            "name": excluded.name,
            "compatibility": excluded.compatibility,
            "mechanisms": excluded.mechanisms,
            "category": excluded.category,
            "aliases": excluded.aliases,
            "notes": excluded.notes,
            "sources": excluded.sources,
            "updated_at": func.now(),
        },
    ).returning(text("(xmax = 0) AS inserted"))

    async with SessionLocal() as session:
        result = await session.execute(upsert)
        flags = result.scalars().all()
        await session.commit()

    inserted = sum(1 for was_inserted in flags if was_inserted)
    log.info("seed.done", total=len(flags), inserted=inserted, updated=len(flags) - inserted)


def main() -> None:
    configure_logging()
    asyncio.run(seed())


if __name__ == "__main__":
    main()
