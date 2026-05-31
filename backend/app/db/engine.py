"""Async engine and session factory.

One engine and connection pool per process, shared across requests.
expire_on_commit=False lets request handlers keep reading ORM objects after
the session commits, which is what the per-request session in session.py
depends on.
"""

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
