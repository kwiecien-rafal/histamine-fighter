"""Tests for the HistamineIngredient model and its database constraints."""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import Compatibility, HistamineMechanism
from app.models import HistamineIngredient


async def test_compatibility_and_mechanisms_round_trip(session: AsyncSession) -> None:
    session.add(
        HistamineIngredient(
            name="Tomato",
            normalized_name="tomato",
            compatibility=Compatibility.INCOMPATIBLE,
            mechanisms=[HistamineMechanism.HIGH_HISTAMINE, HistamineMechanism.LIBERATOR],
            source="SIGHI 2023",
        )
    )
    await session.flush()
    session.expire_all()  # force a real read back from the database

    row = (await session.scalars(select(HistamineIngredient))).one()
    assert row.compatibility is Compatibility.INCOMPATIBLE
    assert row.mechanisms == [HistamineMechanism.HIGH_HISTAMINE, HistamineMechanism.LIBERATOR]


async def test_compatibility_stored_as_value_not_name(session: AsyncSession) -> None:
    # Regression: SQLAlchemy enums default to storing the member name. The column
    # must hold the lowercase value 'incompatible', not 'INCOMPATIBLE'.
    session.add(
        HistamineIngredient(
            name="Tomato",
            normalized_name="tomato",
            compatibility=Compatibility.INCOMPATIBLE,
            source="SIGHI 2023",
        )
    )
    await session.flush()

    stored = await session.scalar(
        text("SELECT compatibility FROM histamine_ingredients WHERE normalized_name = :n"),
        {"n": "tomato"},
    )
    assert stored == "incompatible"


async def test_array_and_timestamp_defaults(session: AsyncSession) -> None:
    session.add(
        HistamineIngredient(
            name="Apple",
            normalized_name="apple",
            compatibility=Compatibility.WELL_TOLERATED,
            source="SIGHI 2023",
        )
    )
    await session.flush()
    session.expire_all()

    row = (await session.scalars(select(HistamineIngredient))).one()
    assert row.mechanisms == []
    assert row.aliases == []
    assert row.created_at is not None


async def test_normalized_name_must_be_unique(session: AsyncSession) -> None:
    session.add(HistamineIngredient(name="Tomato", normalized_name="tomato", source="SIGHI 2023"))
    await session.flush()
    session.add(HistamineIngredient(name="Tomate", normalized_name="tomato", source="SIGHI 2023"))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_check_constraint_rejects_unknown_compatibility(session: AsyncSession) -> None:
    # Insert a raw bad value to prove the database guards the column even when
    # the enum is bypassed.
    with pytest.raises(IntegrityError):
        await session.execute(
            text(
                "INSERT INTO histamine_ingredients "
                "(id, name, normalized_name, compatibility, source) "
                "VALUES (gen_random_uuid(), 'X', 'x', 'banana', 'SIGHI 2023')"
            )
        )
