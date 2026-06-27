"""The operator-set composer model: read it for every composer build, set it from admin.

A single row decides which provider and model the composer uses, honoured by both the
admin triggers and the nightly cron. The provider/model choice is validated at the
route through ``resolve_llm_config`` (the single source of provider truth), never
duplicated here; this service only reads and upserts the singleton. Keys never live
here, only provider and model strings. Never commits; the route owns the transaction.
"""

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.generation_settings import GenerationSettings

log = structlog.get_logger(__name__)


class GenerationSettingsService:
    """Reads and upserts the operator-set composer settings. Never commits."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self) -> GenerationSettings:
        """Return the saved composer settings, or a default when none is saved yet.

        The default mirrors today's behaviour: a fresh install composes with
        ``settings.llm_provider`` and the provider's own default model, so the
        composer works before an admin has set anything. A saved provider that is no
        longer available is still returned as-is; flagging the mismatch is the GET
        endpoint's job, which reports the available providers alongside.
        """
        row = await self._row()
        if row is not None:
            return row
        return GenerationSettings(composer_provider=settings.llm_provider, composer_model=None)

    async def update(
        self, provider: str | None, model: str | None, *, actor: str
    ) -> GenerationSettings:
        """Upsert the singleton with a new provider/model, recording the admin.

        The caller validates the choice through ``resolve_llm_config`` before calling,
        and owns the commit. Never inserts a second row, so the setting stays a singleton.
        """
        row = await self._row()
        if row is None:
            row = GenerationSettings()
            self._session.add(row)
        row.composer_provider = provider
        row.composer_model = model
        row.updated_by = actor
        log.info("compose.settings.updated", provider=provider, model=model, actor=actor)
        return row

    async def _row(self) -> GenerationSettings | None:
        stmt = select(GenerationSettings).order_by(GenerationSettings.created_at)
        return (await self._session.execute(stmt)).scalars().first()
