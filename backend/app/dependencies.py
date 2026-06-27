from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyCookie
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.dish_lookup import DishLookupAgent
from app.agents.learn import LearnAgent
from app.config import settings
from app.core.security import TokenError, decode_access_token
from app.db.session import get_session
from app.embeddings import get_embedder
from app.enums import Role
from app.llm.config import LLMRequestConfig
from app.llm.langchain_factory import build_chat_model
from app.models.user import User
from app.services.composer_streamer import ComposerStreamer
from app.services.daily_service import DailyService
from app.services.generation_settings_service import GenerationSettingsService
from app.services.ingredient_service import IngredientService
from app.services.knowledge_service import KnowledgeService
from app.services.learn_cache_service import LearnCacheService
from app.services.meal_review_service import MealReviewService
from app.services.meal_service import MealService
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


async def get_current_user(
    token: str | None = Depends(_cookie_scheme),
    user_service: UserService = Depends(get_user_service),
) -> User:
    """Resolve the current user from the session cookie, or raise 401.

    Authentication only. The account is re-read from the database every request, so
    a token for a user that has since been removed or deactivated stops working, and
    comparing the token's version against the stored one means a password reset
    invalidates older tokens. Authorization (role) is left to require_admin.
    """
    if token is None:
        raise _unauthorized()
    try:
        claims = decode_access_token(token)
        user_id = UUID(claims.subject)
    except (TokenError, ValueError) as exc:
        raise _unauthorized() from exc
    user = await user_service.get_by_id(user_id)
    if user is None or not user.is_active or user.token_version != claims.token_version:
        raise _unauthorized()
    return user


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


def build_dish_lookup_agent(
    request: Request,
    service: IngredientService = Depends(get_ingredient_service),
    meal_service: MealService = Depends(get_meal_service),
) -> DishLookupAgent:
    """Wire a request-scoped dish-lookup agent: chat model, index, and meal pool.

    ``build_chat_model`` resolves the provider from the request headers and may
    raise the LLM domain errors, which the API boundary maps to status codes. The
    meal pool feeds the verified tier of the alternatives pivot.
    """
    chat = build_chat_model(LLMRequestConfig.from_headers(request))
    return DishLookupAgent(chat=chat, service=service, meal_service=meal_service)


def build_learn_agent(
    request: Request,
    service: KnowledgeService = Depends(get_knowledge_service),
) -> LearnAgent:
    """Wire a request-scoped Learn agent: chat model + vector knowledge retrieval.

    A higher temperature than the dish lookup: the answer is readable educational
    prose, and faithfulness is enforced by the retrieved context and the prompt,
    not by pinning the sampler.
    """
    chat = build_chat_model(LLMRequestConfig.from_headers(request), temperature=0.3)
    return LearnAgent(chat=chat, service=service)
