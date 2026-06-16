from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.dish_lookup import DishLookupAgent
from app.agents.learn import LearnAgent
from app.core.security import TokenError, decode_access_token
from app.db.session import get_session
from app.embeddings import get_embedder
from app.llm.config import LLMRequestConfig
from app.llm.langchain_factory import build_chat_model
from app.models.admin_user import AdminUser
from app.services.admin_service import AdminService
from app.services.ingredient_service import IngredientService
from app.services.knowledge_service import KnowledgeService
from app.services.learn_cache_service import LearnCacheService
from app.services.meal_review_service import MealReviewService
from app.services.meal_service import MealService

# auto_error=False so a missing or malformed header reaches get_current_admin as
# None and is answered with 401 (not HTTPBearer's default 403). The scheme still
# registers Bearer auth in the OpenAPI docs.
_bearer_scheme = HTTPBearer(auto_error=False)


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


def get_admin_service(
    session: AsyncSession = Depends(get_session),
) -> AdminService:
    return AdminService(session)


def get_meal_review_service(
    session: AsyncSession = Depends(get_session),
) -> MealReviewService:
    return MealReviewService(session)


async def get_current_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    admin_service: AdminService = Depends(get_admin_service),
) -> AdminUser:
    """Resolve the admin from the Bearer JWT, or raise 401.

    The account is re-read from the database, so a token for an admin that has
    since been removed stops working. Wired onto admin routes only.
    """
    if credentials is None:
        raise _unauthorized()
    try:
        email = decode_access_token(credentials.credentials)
    except TokenError as exc:
        raise _unauthorized() from exc
    admin = await admin_service.get_by_email(email)
    if admin is None:
        raise _unauthorized()
    return admin


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def build_dish_lookup_agent(
    request: Request,
    service: IngredientService = Depends(get_ingredient_service),
) -> DishLookupAgent:
    """Wire a request-scoped dish-lookup agent: chat model + DB-backed index.

    ``build_chat_model`` resolves the provider from the request headers and may
    raise the LLM domain errors, which the API boundary maps to status codes.
    """
    chat = build_chat_model(LLMRequestConfig.from_headers(request))
    return DishLookupAgent(chat=chat, service=service)


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
