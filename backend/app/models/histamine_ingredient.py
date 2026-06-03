"""ORM model for the curated histamine ingredient index."""

from enum import Enum as StdEnum

from sqlalchemy import Enum, Index, String, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.enums import Compatibility, HistamineMechanism


def _enum_values(enum_cls: type[StdEnum]) -> list[str]:
    """Persist enum values (e.g. 'incompatible'), not member names like 'INCOMPATIBLE'."""
    return [member.value for member in enum_cls.__members__.values()]


class HistamineIngredient(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An ingredient and its histamine tolerance, curated from public references.

    The curated, human-reviewed reference the dish lookup agent reads from.
    Rows are seeded from published sources, never written by the model.
    """

    __tablename__ = "histamine_ingredients"

    name: Mapped[str]
    normalized_name: Mapped[str] = mapped_column(unique=True)
    compatibility: Mapped[Compatibility | None] = mapped_column(
        Enum(
            Compatibility,
            native_enum=False,
            length=24,
            name="compatibility",
            create_constraint=True,
            values_callable=_enum_values,
        )
    )
    mechanisms: Mapped[list[HistamineMechanism]] = mapped_column(
        ARRAY(
            Enum(
                HistamineMechanism,
                native_enum=False,
                length=16,
                name="histamine_mechanism",
                values_callable=_enum_values,
            )
        ),
        default=list,
        server_default=text("'{}'"),
    )
    category: Mapped[str | None]
    aliases: Mapped[list[str]] = mapped_column(
        ARRAY(String), default=list, server_default=text("'{}'")
    )
    notes: Mapped[str | None]
    # At least one reference per row is required; non-emptiness is enforced when seeding.
    sources: Mapped[list[str]] = mapped_column(ARRAY(String))

    __table_args__ = (
        Index(
            "ix_histamine_ingredients_normalized_name_trgm",
            "normalized_name",
            postgresql_using="gin",
            postgresql_ops={"normalized_name": "gin_trgm_ops"},
        ),
    )

    def __repr__(self) -> str:
        return f"<HistamineIngredient {self.name!r}: {self.compatibility}>"
