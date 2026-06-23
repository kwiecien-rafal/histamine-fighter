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
from app.core.ratelimit import limiter
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import get_session
from app.dependencies import get_knowledge_service
from app.enums import Role
from app.main import create_app
from app.models.user import User
from app.services.knowledge_service import KnowledgeService
from tests.fakes import FakeEmbedder

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "supersecret"


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
            # create_all needs the extensions the migrations would otherwise install.
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
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


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    """A deterministic, offline embedder so retrieval tests skip the model."""
    return FakeEmbedder()


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

    def _use_fake_embedder() -> KnowledgeService:
        return KnowledgeService(session, FakeEmbedder())

    app.dependency_overrides[get_session] = _use_test_session
    # The real embedder would download a model on first use; API tests retrieve
    # through the deterministic fake instead.
    app.dependency_overrides[get_knowledge_service] = _use_fake_embedder
    # The limiter is process-wide and counts by client IP, which is the same for
    # every test request; disabled here so unrelated tests cannot trip it. The
    # rate-limit tests re-enable it explicitly.
    limiter.enabled = False
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            yield http_client
    finally:
        limiter.enabled = True


@pytest_asyncio.fixture
async def admin_user(session: AsyncSession) -> User:
    """An active admin account on the test transaction."""
    user = User(
        email=ADMIN_EMAIL,
        password_hash=hash_password(ADMIN_PASSWORD),
        role=Role.ADMIN,
    )
    session.add(user)
    await session.flush()
    return user


@pytest_asyncio.fixture
async def authenticated_client(client: AsyncClient, admin_user: User) -> AsyncClient:
    """A client that has logged in, so it carries the httpOnly session cookie.

    Goes through the real /login flow rather than minting a header by hand: the
    server sets the cookie and httpx keeps it in the jar for the rest of the test.
    """
    resp = await client.post(
        "/admin/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert resp.status_code == 200
    return client
