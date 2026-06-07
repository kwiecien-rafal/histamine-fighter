from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.v1 import histamine, meals
from app.config import settings
from app.core.logging import configure_logging
from app.db.engine import engine
from app.llm.errors import LLMConfigError, ProviderNotAvailableError

logger = structlog.get_logger(__name__)


def _llm_error_handler(
    status_code: int,
) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
    """Translate an LLM-layer domain error into an HTTP response at the boundary."""

    async def handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=status_code, content={"detail": str(exc)})

    return handler


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
    # The LLM layer raises domain errors, not HTTPException; map them here so a
    # bad provider/key is a 400 and a reserved provider is a 501.
    app.add_exception_handler(LLMConfigError, _llm_error_handler(400))
    app.add_exception_handler(ProviderNotAvailableError, _llm_error_handler(501))

    app.include_router(histamine.router)
    app.include_router(meals.router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
