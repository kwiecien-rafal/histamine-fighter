import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

from app.api.admin import auth as admin_auth
from app.api.admin import daily as admin_daily
from app.api.admin import meals as admin_meals
from app.api.v1 import daily, histamine, learn, meals
from app.config import settings
from app.core.logging import configure_logging
from app.core.ratelimit import limiter
from app.db.engine import engine
from app.embeddings import warm_up_embedder
from app.llm.errors import LLMConfigError, LLMInvocationError, ProviderNotAvailableError

logger = structlog.get_logger(__name__)

# State-changing methods guarded by the Origin check. GET/HEAD/OPTIONS are safe and
# are how CORS preflight and simple reads flow, so they pass through untouched.
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Hardening headers set on every response (CLAUDE section 20). HSTS is conditional
# and added in the middleware, since it only applies once traffic is HTTPS.
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


def _llm_error_handler(
    status_code: int,
) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
    """Translate an LLM-layer domain error into an HTTP response at the boundary."""

    async def handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=status_code, content={"detail": str(exc)})

    return handler


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Set up logging, check the database, and warm the embedder before serving.

    The embedder is loaded here (off the event loop) so a missing or corrupt
    model fails the deploy at startup instead of stalling the first user's
    request on a model download.
    """
    configure_logging()
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        logger.exception("startup.database_unreachable")
        raise
    embedder = await asyncio.to_thread(warm_up_embedder)
    logger.info("startup.embedder_ready", model=embedder.model_name)
    logger.info("startup.complete")
    yield
    await engine.dispose()
    logger.info("shutdown.complete")


def create_app() -> FastAPI:
    app = FastAPI(title="Histamine Fighter", debug=settings.debug, lifespan=lifespan)
    # allow_credentials is required for the session cookie to ride on the SPA's
    # requests. It is safe only because allow_origins is an explicit list, never "*".
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # The LLM layer raises domain errors, not HTTPException; map them here so a
    # bad provider/key is a 400, a reserved provider a 501, and a failed model
    # call (e.g. a model that cannot emit structured output) a 502.
    app.add_exception_handler(LLMConfigError, _llm_error_handler(400))
    app.add_exception_handler(ProviderNotAvailableError, _llm_error_handler(501))
    app.add_exception_handler(LLMInvocationError, _llm_error_handler(502))

    # slowapi looks the limiter up on app.state; routes opt in via its decorator.
    # Own handler rather than slowapi's stock one: its signature is too narrow
    # for Starlette's typing, and this keeps the error shape consistent.
    app.state.limiter = limiter

    async def _rate_limited(request: Request, exc: Exception) -> JSONResponse:
        detail = exc.detail if isinstance(exc, RateLimitExceeded) else str(exc)
        return JSONResponse(status_code=429, content={"detail": f"Rate limit exceeded: {detail}"})

    app.add_exception_handler(RateLimitExceeded, _rate_limited)

    @app.middleware("http")
    async def enforce_origin(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Reject a state-changing request from an Origin we do not trust.

        Defense in depth behind the session cookie's SameSite=Lax: a cross-site
        browser request carrying an untrusted Origin is refused before it reaches a
        route. A request with no Origin (same-origin, or a non-browser client) is
        left to the cookie's SameSite rule.
        """
        origin = request.headers.get("origin")
        if (
            request.method in _UNSAFE_METHODS
            and origin is not None
            and origin not in settings.cors_origins
        ):
            return JSONResponse(
                status_code=403, content={"detail": "Cross-origin request rejected."}
            )
        return await call_next(request)

    @app.middleware("http")
    async def set_security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Attach baseline hardening headers to every response.

        Defends against clickjacking (X-Frame-Options), MIME sniffing (nosniff), and
        referrer leakage (Referrer-Policy). HSTS is added only on a public
        deployment, where TLS is terminated and forcing HTTPS is safe. A strict CSP
        for the SPA belongs in the frontend server, not on these JSON responses.
        """
        response = await call_next(request)
        response.headers.update(_SECURITY_HEADERS)
        if settings.public_deployment:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response

    @app.middleware("http")
    async def log_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Log each request's start and outcome, with a short id bound into the
        context so every downstream log line (agent, tools, retrieval) carries it.
        The id is also returned as ``X-Request-ID`` so an operator handed a failed
        response can find its log lines."""
        request_id = uuid4().hex[:8]
        structlog.contextvars.bind_contextvars(request_id=request_id)
        logger.info("request.start", method=request.method, path=request.url.path)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("request.failed", ms=_elapsed_ms(started))
            raise
        else:
            # Domain errors become responses via the exception handlers inside this
            # call, so they arrive here as a normal 4xx/5xx — not the except branch.
            response.headers["X-Request-ID"] = request_id
            emit = logger.warning if response.status_code >= 500 else logger.info
            emit("request.done", status=response.status_code, ms=_elapsed_ms(started))
            return response
        finally:
            structlog.contextvars.clear_contextvars()

    app.include_router(histamine.router)
    app.include_router(meals.router)
    app.include_router(learn.router)
    app.include_router(daily.router)
    app.include_router(admin_auth.router)
    app.include_router(admin_meals.router)
    app.include_router(admin_daily.router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


app = create_app()
