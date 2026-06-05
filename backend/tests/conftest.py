"""Shared fixtures for database-backed tests.

Integration tests run against a dedicated ``<db>_test`` database on the same
Postgres server used for development. The schema is built once per session
from the models (kept in step with the migrations by ``alembic check``), and
each test runs inside a transaction that is rolled back, so tests stay
isolated and the database stays empty.

Only tests that request the ``session`` fixture touch Postgres. The pure unit
tests do not, so they still run without a database.
"""

import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import URL, make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import app.models  # noqa: F401  (registers the models on Base.metadata)
from app.config import settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app


def _test_database_url() -> URL:
    url = make_url(settings.database_url)
    return url.set(database=f"{url.database}_test")


TEST_DATABASE_URL = _test_database_url()


async def _create_database_and_schema() -> None:
    """Recreate the test database and build the schema from the models."""
    db_name = TEST_DATABASE_URL.database
    admin_engine = create_async_engine(settings.database_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin_engine.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
            await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    finally:
        await admin_engine.dispose()

    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.begin() as conn:
            # create_all needs the extension the migration would otherwise install.
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            await conn.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def _database_schema() -> None:
    asyncio.run(_create_database_and_schema())


@pytest_asyncio.fixture
async def session(_database_schema: None) -> AsyncIterator[AsyncSession]:
    """A session bound to a transaction that is rolled back after each test.

    Tests should ``flush`` rather than ``commit`` so the rollback keeps the
    database empty between tests.
    """
    engine = create_async_engine(TEST_DATABASE_URL)
    connection = await engine.connect()
    transaction = await connection.begin()
    db = AsyncSession(bind=connection, expire_on_commit=False)
    try:
        yield db
    finally:
        await db.close()
        # A failed flush rolls the transaction back itself, so only roll back
        # when it is still active to avoid a spurious warning.
        if transaction.is_active:
            await transaction.rollback()
        await connection.close()
        await engine.dispose()


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """An HTTP client whose requests run on the test transaction.

    get_session is overridden to hand each request the same rolled-back session
    the test uses, so rows a test adds are visible to the endpoint and cleaned
    up afterwards.
    """
    app = create_app()

    async def _use_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
