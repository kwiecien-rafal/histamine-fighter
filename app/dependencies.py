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
from app.llm.request import RequestLLM
from app.models.user import User
from app.services.auth_service import AuthService
from app.services.composer_streamer import ComposerStreamer
from app.services.daily_service import DailyService
from app.services.dish_lookup_service import DishLookupService
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


def get_dish_lookup_service(
    cache: LookupCacheService = Depends(get_lookup_cache_service),
) -> DishLookupService:
    return DishLookupService(cache)


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
    """The process-wide outbound HTTP client, created in the lifespan."""
    client = request.app.state.http_client
    if not isinstance(client, httpx.AsyncClient):  # pragma: no cover - lifespan contract
        raise RuntimeError("HTTP client not initialised; app started without lifespan.")
    return client


def get_email_service(
    client: httpx.AsyncClient = Depends(get_http_client),
) -> EmailService:
    return EmailService(client)


def get_auth_service(
    session: AsyncSession = Depends(get_session),
    magic_links: MagicLinkService = Depends(get_magic_link_service),
    users: UserService = Depends(get_user_service),
    quota: QuotaService = Depends(get_quota_service),
    emails: EmailService = Depends(get_email_service),
    http_client: httpx.AsyncClient = Depends(get_http_client),
) -> AuthService:
    return AuthService(session, magic_links, users, quota, emails, http_client)


async def get_composer_streamer(
    session: AsyncSession = Depends(get_session),
) -> ComposerStreamer:
    """Wire the live composer for the admin trigger."""
    gen_settings = await GenerationSettingsService(session).get()
    chat = build_chat_model(
        LLMRequestConfig(
            provider=gen_settings.composer_provider, model=gen_settings.composer_model
        ),
        temperature=settings.compose_temperature,
    )
    return ComposerStreamer(chat, get_embedder())


async def _resolve_session_user(token: str | None, user_service: UserService) -> User | None:
    """Resolve the session cookie to a live user, or None."""
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
    """Resolve the current user from the session cookie, or raise 401."""
    user = await _resolve_session_user(token, user_service)
    if user is None:
        raise _unauthorized()
    return user


async def get_current_user_optional(
    token: str | None = Depends(_cookie_scheme),
    user_service: UserService = Depends(get_user_service),
) -> User | None:
    """Resolve the current user if a valid session rides the request, else None."""
    return await _resolve_session_user(token, user_service)


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Gate a route to admins, running get_current_user (authN) first."""
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


async def get_request_llm_config(
    request: Request,
    user: User | None = Depends(get_current_user_optional),
    quota: QuotaService = Depends(get_quota_service),
) -> RequestLLM:
    """Resolve the request's LLM config, mediating the shared tier."""
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
    """The request's resolved config, if any route dependency resolved one."""
    resolved = getattr(request.state, _REQUEST_LLM_STATE, None)
    return resolved if isinstance(resolved, RequestLLM) else None


def build_dish_lookup_agent(
    resolved: RequestLLM = Depends(get_request_llm_config),
    service: IngredientService = Depends(get_ingredient_service),
    meal_service: MealService = Depends(get_meal_service),
) -> DishLookupAgent:
    """Wire a request-scoped dish-lookup agent: chat model, index, and meal pool."""
    chat = build_chat_model(resolved.config, allow_server_key=not settings.public_deployment)
    return DishLookupAgent(chat=chat, service=service, meal_service=meal_service)


def build_recipe_agent(
    resolved: RequestLLM = Depends(get_request_llm_config),
    service: IngredientService = Depends(get_ingredient_service),
) -> RecipeAgent:
    """Wire a request-scoped recipe agent; same key rules as the dish lookup."""
    chat = build_chat_model(resolved.config, allow_server_key=not settings.public_deployment)
    return RecipeAgent(chat=chat, service=service)


def build_learn_agent(
    resolved: RequestLLM = Depends(get_request_llm_config),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> LearnAgent:
    """Wire a request-scoped Learn agent: chat model + vector knowledge retrieval."""
    chat = build_chat_model(
        resolved.config, temperature=0.3, allow_server_key=not settings.public_deployment
    )
    return LearnAgent(chat=chat, service=service)
