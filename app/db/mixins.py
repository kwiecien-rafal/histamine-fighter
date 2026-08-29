"""Column mixins shared by the ORM models.

Every table needs a UUID primary key and created/updated timestamps, so they
live here instead of being repeated on each model.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


class UUIDPrimaryKeyMixin:
    """UUID primary key, generated in Python rather than by the database."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    """Adds created_at and updated_at columns.

    updated_at is bumped by SQLAlchemy on ORM updates, so a raw SQL UPDATE
    will leave it untouched.
    """

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
