from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

import httpx
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyCookie
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.dish_lookup import DishLookupAgent
from app.agents.learn import LearnAgent
from app.agents.recipe import RecipeAgent
from app.config import settings
from app.core.client_ip import client_ip, ip_bucket
from app.core.security import TokenError, decode_access_token
from app.db.engine import SessionLocal
from app.db.session import get_session
from app.embeddings import get_embedder
from app.enums import Role
from app.llm.config import LLMRequestConfig
from app.llm.errors import ProviderNotAvailableError
from app.llm.langchain_factory import build_chat_model
from app.models.user import User
from app.services.composer_streamer import ComposerStreamer
from app.services.daily_service import DailyService
from app.services.email_service import EmailService
from app.services.generation_settings_service import GenerationSettingsService
from app.services.ingredient_service import IngredientService
from app.services.knowledge_service import KnowledgeService
from app.services.learn_cache_service import LearnCacheService
from app.services.lookup_cache_service import LookupCacheService
from app.services.magic_link_service import MagicLinkService
from app.services.meal_review_service import MealReviewService
from app.services.meal_service import MealService
from app.services.quota_service import QuotaService
from app.services.saved_meal_service import SavedMealService
from app.services.user_service import UserService

# auto_error=False so a missing cookie reaches get_current_user as None and is
# answered with 401. The scheme reads the session cookie and documents cookie auth
# in the OpenAPI docs.
_cookie_scheme = APIKeyCookie(name=settings.session_cookie_name, auto_error=False)


def get_ingredient_service(
    session: AsyncSession = Depends(get_session),
) -> IngredientService:
    return IngredientService(session)


def get_knowledge_service(
    session: AsyncSession = Depends(get_session),
) -> KnowledgeService:
    # get_embedder returns the process-wide singleton; the service takes it by
    # constructor so a test can inject a deterministic stand-in instead.
    return KnowledgeService(session, get_embedder())


def get_learn_cache_service(
    session: AsyncSession = Depends(get_session),
) -> LearnCacheService:
    return LearnCacheService(session)


def get_lookup_cache_service(
    session: AsyncSession = Depends(get_session),
    ingredient_service: IngredientService = Depends(get_ingredient_service),
) -> LookupCacheService:
    # The ingredient service powers the re-grade that a cached assessment must
    # pass before it is served.
    return LookupCacheService(session, ingredient_service)


def get_meal_service(
    session: AsyncSession = Depends(get_session),
) -> MealService:
    # Same embedder singleton as the knowledge retrieval; injected by constructor
    # so a test can swap in a deterministic stand-in.
    return MealService(session, get_embedder())


def get_user_service(
    session: AsyncSession = Depends(get_session),
) -> UserService:
    return UserService(session)


def get_saved_meal_service(
    session: AsyncSession = Depends(get_session),
) -> SavedMealService:
    return SavedMealService(session)


def get_meal_review_service(
    session: AsyncSession = Depends(get_session),
) -> MealReviewService:
    return MealReviewService(session)


def get_daily_service(
    session: AsyncSession = Depends(get_session),
) -> DailyService:
    return DailyService(session)


def get_generation_settings_service(
    session: AsyncSession = Depends(get_session),
) -> GenerationSettingsService:
    return GenerationSettingsService(session)


def get_quota_service() -> QuotaService:
    # Deliberately not Depends(get_session): the service opens and commits its own
    # short transactions (see its module docstring), so it takes the factory.
    return QuotaService(SessionLocal)


def get_magic_link_service(
    session: AsyncSession = Depends(get_session),
) -> MagicLinkService:
    return MagicLinkService(session)


def get_http_client(request: Request) -> httpx.AsyncClient:
    """The process-wide outbound HTTP client, created in the lifespan.

    Everything that leaves the backend over plain HTTP (Resend, Turnstile, OAuth
    token exchange) goes through this one pooled client; tests override this
    dependency to keep the suite offline.
    """
    client = request.app.state.http_client
    if not isinstance(client, httpx.AsyncClient):  # pragma: no cover - lifespan contract
        raise RuntimeError("HTTP client not initialised; app started without lifespan.")
    return client


def get_email_service(
    client: httpx.AsyncClient = Depends(get_http_client),
) -> EmailService:
    return EmailService(client)


async def get_composer_streamer(
    session: AsyncSession = Depends(get_session),
) -> ComposerStreamer:
    """Wire the live composer for the admin trigger.

    Board composition is an operator action, not a per-user request, so the provider
    resolves from the operator-set ``GenerationSettings`` (shared with the cron
    scripts), never from X-LLM headers. A bad saved config raises here (mapped to
    400/501 at the boundary) before the stream opens; a tool-incapable model fails
    later as a stream error.
    """
    gen_settings = await GenerationSettingsService(session).get()
    chat = build_chat_model(
        LLMRequestConfig(
            provider=gen_settings.composer_provider, model=gen_settings.composer_model
        ),
        temperature=settings.compose_temperature,
    )
    return ComposerStreamer(chat, get_embedder())


async def _resolve_session_user(token: str | None, user_service: UserService) -> User | None:
    """Resolve the session cookie to a live user, or None.

    The account is re-read from the database every request, so a token for a user
    that has since been removed or deactivated stops working, and comparing the
    token's version against the stored one means a credential reset invalidates
    older tokens.
    """
    if token is None:
        return None
    try:
        claims = decode_access_token(token)
        user_id = UUID(claims.subject)
    except (TokenError, ValueError):
        return None
    user = await user_service.get_by_id(user_id)
    if user is None or not user.is_active or user.token_version != claims.token_version:
        return None
    return user


async def get_current_user(
    token: str | None = Depends(_cookie_scheme),
    user_service: UserService = Depends(get_user_service),
) -> User:
    """Resolve the current user from the session cookie, or raise 401.

    Authentication only. Authorization (role) is left to require_admin.
    """
    user = await _resolve_session_user(token, user_service)
    if user is None:
        raise _unauthorized()
    return user


async def get_current_user_optional(
    token: str | None = Depends(_cookie_scheme),
    user_service: UserService = Depends(get_user_service),
) -> User | None:
    """Resolve the current user if a valid session rides the request, else None.

    For routes that serve both anonymous and signed-in callers (the shared LLM
    tier); the route decides whether anonymity is a 401 or just a different path.
    """
    return await _resolve_session_user(token, user_service)


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Gate a route to admins, running get_current_user (authN) first.

    Authorization only: the user is already authenticated, so a non-admin is a
    deliberate 403 (authenticated but not allowed), distinct from the 401 an
    unauthenticated request gets.
    """
    if user.role is not Role.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required.",
        )
    return user


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
    )


# The provider name the SPA sends for the operator-funded tier. Deliberately not
# a Provider enum member: resolve_llm_config never sees it, so the composer, the
# cron scripts, and selectable_providers() cannot pick it up by accident.
SHARED_PROVIDER = "shared"

# Key under which the resolved config is stashed on request.state, so the
# charge-leak backstop middleware can see what the route left unspent.
_REQUEST_LLM_STATE = "request_llm"


@dataclass
class RequestLLM:
    """A resolved per-request LLM config and its deferred shared-tier charge.

    The charge is held back until the route reaches the actual model call, past
    body validation, the burst limiter, and the Learn cache, so a rejected or
    cached request never spends the daily allowance. ``charge`` is a one-shot:
    the first call spends, later calls are no-ops, and a failed charge does not
    re-arm (the request is already being rejected). A route that resolves the
    shared config but then makes no model call (a cache hit) calls ``waive``, so
    the charge is released without spending it and the leak backstop stays quiet.
    """

    config: LLMRequestConfig
    # True only when the config was pinned to the operator-funded shared tier.
    # Explicit rather than inferred from ``pending``: ``charge()`` consumes the
    # callable before the lookup-cache write gate needs the answer.
    shared: bool = False
    _charge: Callable[[], Awaitable[None]] | None = None

    @property
    def pending(self) -> bool:
        """Whether a shared-tier charge is still unspent (the backstop's check)."""
        return self._charge is not None

    async def charge(self) -> None:
        charge = self._charge
        if charge is None:
            return
        self._charge = None
        await charge()

    def waive(self) -> None:
        """Release the pending shared-tier charge without spending it.

        For a route that resolved the shared config but served the request with no
        model call (a Learn cache hit): the answer costs nothing, so the daily
        allowance is untouched and the charge-leak backstop must not read the
        deliberate skip as a forgotten charge. Unlike a forgotten charge this is an
        expected outcome, so it stays silent.
        """
        self._charge = None


async def get_request_llm_config(
    request: Request,
    user: User | None = Depends(get_current_user_optional),
    quota: QuotaService = Depends(get_quota_service),
) -> RequestLLM:
    """Resolve the request's LLM config, mediating the shared tier.

    BYO-key and Ollama requests pass through untouched. ``shared`` requires a
    session (401 anonymous) and a configured server key (501 without one, the
    self-hoster answer), then pins the server's OpenAI key and model while every
    other X-LLM header is ignored, so no client input can steer what the operator
    pays for. The daily quota charge is *deferred* onto the returned object and
    run by the route at the model-call boundary, so a cache hit, a 422, or a
    burst-limited 429 never spends it. The result is also stashed on
    ``request.state`` for the charge-leak backstop middleware.
    """
    cfg = LLMRequestConfig.from_headers(request)
    # Same normalization _parse_provider applies, so "Shared" from a hand-written
    # client behaves like the SPA's "shared".
    if (cfg.provider or "").strip().lower() != SHARED_PROVIDER:
        return _arm_request_llm(request, RequestLLM(config=cfg))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to use the shared free tier, or bring your own key.",
        )
    if settings.openai_api_key is None:
        raise ProviderNotAvailableError(
            "The shared tier is not configured on this deployment (no server-side "
            "OpenAI key). Bring your own key or use Ollama."
        )
    user_id, ip = user.id, ip_bucket(client_ip(request))

    async def _charge() -> None:
        await quota.charge_shared(user_id, ip)

    pinned = LLMRequestConfig(
        provider="openai", model=settings.shared_model, api_key=settings.openai_api_key
    )
    return _arm_request_llm(request, RequestLLM(config=pinned, shared=True, _charge=_charge))


def _arm_request_llm(request: Request, resolved: RequestLLM) -> RequestLLM:
    setattr(request.state, _REQUEST_LLM_STATE, resolved)
    return resolved


def stashed_request_llm(request: Request) -> RequestLLM | None:
    """The request's resolved config, if any route dependency resolved one.

    Read only by the charge-leak backstop middleware in main.py; routes get the
    same instance through Depends (FastAPI caches the dependency per request)
    and call ``charge()`` on it directly.
    """
    resolved = getattr(request.state, _REQUEST_LLM_STATE, None)
    return resolved if isinstance(resolved, RequestLLM) else None


def build_dish_lookup_agent(
    resolved: RequestLLM = Depends(get_request_llm_config),
    service: IngredientService = Depends(get_ingredient_service),
    meal_service: MealService = Depends(get_meal_service),
) -> DishLookupAgent:
    """Wire a request-scoped dish-lookup agent: chat model, index, and meal pool.

    ``build_chat_model`` resolves the provider from the mediated request config
    and may raise the LLM domain errors, which the API boundary maps to status
    codes. On a public deployment the operator's key is reserved for the metered
    shared tier, so a keyless BYO request is refused rather than billed to it;
    self-hosted, the operator's configured provider stays the free default. The
    meal pool feeds the verified tier of the alternatives pivot.
    """
    chat = build_chat_model(resolved.config, allow_server_key=not settings.public_deployment)
    return DishLookupAgent(chat=chat, service=service, meal_service=meal_service)


def build_recipe_agent(
    resolved: RequestLLM = Depends(get_request_llm_config),
    service: IngredientService = Depends(get_ingredient_service),
) -> RecipeAgent:
    """Wire a request-scoped recipe agent; same key rules as the dish lookup.

    The ingredient service powers the code-side scan of the drafted steps for
    index-avoid terms kept off the list.
    """
    chat = build_chat_model(resolved.config, allow_server_key=not settings.public_deployment)
    return RecipeAgent(chat=chat, service=service)


def build_learn_agent(
    resolved: RequestLLM = Depends(get_request_llm_config),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> LearnAgent:
    """Wire a request-scoped Learn agent: chat model + vector knowledge retrieval.

    A higher temperature than the dish lookup: the answer is readable educational
    prose, and faithfulness is enforced by the retrieved context and the prompt,
    not by pinning the sampler.
    """
    chat = build_chat_model(
        resolved.config, temperature=0.3, allow_server_key=not settings.public_deployment
    )
    return LearnAgent(chat=chat, service=service)
