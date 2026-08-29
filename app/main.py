import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import structlog
from fastapi import FastAPI, Request, Response
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.admin import auth as admin_auth
from app.api.admin import compose as admin_compose
from app.api.admin import daily as admin_daily
from app.api.admin import meals as admin_meals
from app.api.v1 import auth, daily, histamine, learn, meals, saved_meals
from app.config import settings
from app.core.client_ip import warn_if_unproxied
from app.core.disposable_domains import warm_blocklist
from app.core.logging import configure_logging
from app.core.ratelimit import limiter
from app.core.turnstile import TurnstileError
from app.db.engine import engine
from app.dependencies import stashed_request_llm
from app.embeddings import warm_up_embedder
from app.llm.errors import (
    LLMConfigError,
    LLMInvocationError,
    LLMRejectedError,
    ProviderNotAvailableError,
)
from app.services.email_service import EmailDeliveryError
from app.services.quota_service import QuotaExceededError
from app.web import router as web_router
from app.web.deps import STATIC_DIR, templates

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

# Path prefixes whose errors must stay machine-readable. Everything else is a page a
# browser asked for, so its 404 is rendered as one.
_JSON_ERROR_PREFIXES = ("/api/", "/admin/", "/static/")


def _is_same_origin(request: Request, origin: str) -> bool:
    """Whether an Origin header names this app's own address.

    The server-rendered pages post their forms back to themselves, so same-origin
    writes must pass without the app having to list its own address in
    CORS_ORIGINS. Host and port only: behind a TLS-terminating proxy the app can
    see http where the browser sent https, and comparing schemes there would
    refuse its own forms.
    """
    return urlsplit(origin).netloc == request.url.netloc


async def _not_found_page(request: Request, exc: Exception) -> Response:
    """Answer a browser-facing 404 with a page instead of a JSON body.

    Covers the miss no route can: an unrouted path (the commonest 404 a site serves)
    never reaches a handler of ours. Only 404s outside the API prefixes render a page;
    every other status keeps FastAPI's own handler, so the JSON error contract and the
    headers that ride on it (``WWW-Authenticate`` on a 401) are untouched.
    """
    if not isinstance(exc, StarletteHTTPException):  # pragma: no cover - handler contract
        raise exc
    if exc.status_code != 404 or request.url.path.startswith(_JSON_ERROR_PREFIXES):
        return await http_exception_handler(request, exc)
    # Starlette's stock detail for an unrouted path is the bare status phrase; a route
    # that raises its own says something useful, so only the stock one is replaced.
    message = "There is no page at that address." if exc.detail == "Not Found" else exc.detail
    return templates.TemplateResponse(
        request, "not_found.html", {"message": message}, status_code=404
    )


def _warn_on_risky_deployment() -> None:
    """Surface production settings that silently weaken the public hardening.

    None can be proven wrong from config alone (a self-hoster may legitimately run
    without them), so each warns loudly rather than refusing to boot. All are
    no-ops off a public or production deployment. The proxy-header trust cannot be
    seen from config, so it is checked per request in client_ip instead.
    """
    if settings.public_deployment and settings.turnstile_secret_key is None:
        logger.warning(
            "startup.turnstile_unconfigured",
            hint="TURNSTILE_SECRET_KEY is unset on a public deployment; the "
            "magic-link form has no bot gate beyond the per-IP daily send cap.",
        )
    if settings.is_production and not settings.public_deployment:
        logger.warning(
            "startup.production_without_public_deployment",
            hint="DEBUG is off but PUBLIC_DEPLOYMENT is not set, so the session "
            "cookie is not Secure and HSTS is off. Set PUBLIC_DEPLOYMENT=true when "
            "serving over TLS.",
        )


def _domain_error_handler(
    status_code: int,
) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
    """Translate a domain error into an HTTP response at the boundary."""

    async def handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=status_code, content={"detail": str(exc)})

    return handler


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Set up logging, check the database, and warm the embedder before serving.

    The embedder is loaded here (off the event loop) so a missing or corrupt
    model fails the deploy at startup instead of stalling the first user's
    request on a model download. The shared httpx client serves every outbound
    call (Resend, Turnstile, OAuth), so connections are pooled process-wide.
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
    warm_blocklist()
    _warn_on_risky_deployment()
    app.state.http_client = httpx.AsyncClient(timeout=10)
    logger.info("startup.complete")
    yield
    await app.state.http_client.aclose()
    await engine.dispose()
    logger.info("shutdown.complete")


def create_app() -> FastAPI:
    app = FastAPI(title="Histamine Fighter", debug=settings.debug, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_exception_handler(LLMConfigError, _domain_error_handler(400))
    app.add_exception_handler(ProviderNotAvailableError, _domain_error_handler(501))
    app.add_exception_handler(LLMRejectedError, _domain_error_handler(400))
    app.add_exception_handler(LLMInvocationError, _domain_error_handler(502))

    app.add_exception_handler(TurnstileError, _domain_error_handler(400))
    app.add_exception_handler(EmailDeliveryError, _domain_error_handler(502))
    app.add_exception_handler(StarletteHTTPException, _not_found_page)

    async def _quota_exceeded(request: Request, exc: Exception) -> JSONResponse:
        """429 for an exhausted daily quota."""
        if not isinstance(exc, QuotaExceededError):
            raise exc
        if exc.scope == "signup_ip":
            detail = "Too many new accounts from this network today. Try again tomorrow."
        else:
            detail = (
                "Daily free-tier limit reached. Bring your own key in Settings, "
                "or come back tomorrow."
            )
        return JSONResponse(
            status_code=429,
            content={
                "detail": detail,
                "quota": {
                    "scope": exc.scope,
                    "used": exc.used,
                    "limit": exc.limit,
                    "resets_at": exc.resets_at.isoformat(),
                },
            },
        )

    app.add_exception_handler(QuotaExceededError, _quota_exceeded)

    app.state.limiter = limiter

    async def _rate_limited(request: Request, exc: Exception) -> JSONResponse:
        detail = exc.detail if isinstance(exc, RateLimitExceeded) else str(exc)
        return JSONResponse(status_code=429, content={"detail": f"Rate limit exceeded: {detail}"})

    app.add_exception_handler(RateLimitExceeded, _rate_limited)

    @app.middleware("http")
    async def settle_leaked_shared_charge(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Tripwire for a route that ran the shared tier but never charged it.

        The mechanism is the route calling ``resolved.charge()`` at its model-call
        boundary; this backstop only catches the forgotten line. A successful
        response with a still-pending charge is billed here anyway (a coding bug
        must never open a free tier) and logged as an error so it gets fixed. The
        late QuotaExceededError is swallowed: the response already went out.
        """
        response = await call_next(request)
        resolved = stashed_request_llm(request)
        if resolved is not None and resolved.pending and response.status_code < 400:
            logger.error("llm.shared_charge_leaked", path=request.url.path)
            try:
                await resolved.charge()
            except QuotaExceededError:
                pass
        return response

    @app.middleware("http")
    async def enforce_origin(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Reject a state-changing request from an Origin we do not trust.

        Defense in depth behind the session cookie's SameSite=Lax: a cross-site
        browser request carrying an untrusted Origin is refused before it reaches a
        route. A request with no Origin (a non-browser client) is left to the
        cookie's SameSite rule. Browsers do send one on a same-origin form post, so
        the app's own address is trusted alongside the configured origins.
        """
        origin = request.headers.get("origin")
        if (
            request.method in _UNSAFE_METHODS
            and origin is not None
            and origin not in settings.cors_origins
            and not _is_same_origin(request, origin)
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
        deployment, where TLS is terminated and forcing HTTPS is safe.
        """
        response = await call_next(request)
        response.headers.update(_SECURITY_HEADERS)
        # Every page renders the signed-in account into its masthead, so a shared
        # cache must key on the session cookie rather than serve one visitor's
        # shell to another.
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers.setdefault("Vary", "Cookie")
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
        warn_if_unproxied(request)
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
    app.include_router(saved_meals.router)
    app.include_router(auth.router)
    app.include_router(admin_auth.router)
    app.include_router(admin_meals.router)
    app.include_router(admin_daily.router)
    app.include_router(admin_compose.router)
    # The server-rendered pages take the bare paths, below every /api/v1 and /admin
    # prefix, so the JSON API keeps its own namespace and the two cannot collide.
    app.include_router(web_router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


app = create_app()
