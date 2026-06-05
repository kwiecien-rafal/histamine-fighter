from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.v1 import histamine, meals
from app.config import settings
from app.core.logging import configure_logging
from app.db.engine import engine

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Set up logging and check the database is reachable before serving requests."""
    configure_logging()
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        logger.exception("startup.database_unreachable")
        raise
    logger.info("startup.complete")
    yield
    await engine.dispose()
    logger.info("shutdown.complete")


def create_app() -> FastAPI:
    app = FastAPI(title="Histamine Fighter", debug=settings.debug, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(histamine.router)
    app.include_router(meals.router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
