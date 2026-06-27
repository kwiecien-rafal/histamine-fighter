"""ORM model for the operator-set composer model.

A single row holding which provider and model the composer uses, set by an admin
and honoured by both the admin triggers and the nightly cron. Only the provider and
model strings live here, never an API key or a base URL: keys stay in the environment
(CLAUDE section 13) so the database never holds a secret, and Ollama's base URL stays
env-only so a stored value cannot point the server at an arbitrary host. A unique
``is_singleton`` column pins the table to one row, and ``updated_by`` records the admin
who last changed it.
"""

from sqlalchemy import UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class GenerationSettings(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The operator-set composer provider and model; one row, enforced at the database.

    ``is_singleton`` is always True under a unique constraint, so a second row is a
    database error rather than a convention the service merely tries to hold to.
    """

    __tablename__ = "generation_settings"
    __table_args__ = (UniqueConstraint("is_singleton", name="uq_generation_settings_singleton"),)

    composer_provider: Mapped[str | None] = mapped_column(default=None)
    composer_model: Mapped[str | None] = mapped_column(default=None)
    updated_by: Mapped[str | None] = mapped_column(default=None)
    is_singleton: Mapped[bool] = mapped_column(default=True, server_default=text("true"))

    def __repr__(self) -> str:
        return f"<GenerationSettings {self.composer_provider}/{self.composer_model}>"
