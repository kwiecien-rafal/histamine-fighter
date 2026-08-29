"""The admin panel: the review queues, the moderation actions, and the live composer.

Every write drives the JSON admin router's own handler, so the approval stamps, the
index re-check that gates an edit, and the manual-meal safety gate keep exactly one
implementation; these routes turn a form into that call and its refusal into page copy.

Composition is the exception. Its endpoints stream Server-Sent Events over a POST, which
neither a form nor htmx's SSE extension can consume (EventSource is GET-only), so the
panel's one script drives them directly. They stay POST deliberately: a run spends real
tokens and writes a row, so it belongs behind the same Origin check as every other write.

Values are shown as the domain states them — ``pending``, ``breakfast``, the model's own
name — rather than in the site's branded wording, so admin tooling stays neutral and
readable (CLAUDE section 19).

Pages live at ``/admin`` and their writes under ``/admin/ui/*``, clear of the JSON routers
at ``/admin/{auth,meals,daily,compose}``.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Literal, NamedTuple, TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from slowapi.errors import RateLimitExceeded
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.client_ip import client_ip
from app.core.security import create_access_token
from app.core.session_cookie import set_session_cookie
from app.db.session import get_session
from app.dependencies import (
    get_current_user_optional,
    get_daily_service,
    get_generation_settings_service,
    get_ingredient_service,
    get_meal_review_service,
    get_meal_service,
    get_user_service,
)
from app.enums import ApprovalStatus, MealType, Role
from app.llm.errors import LLMError
from app.llm.providers import Provider, selectable_providers
from app.models import CuratedMeal, DailySuggestion
from app.models.user import User
from app.schemas.admin import (
    MAX_EMAIL_CHARS,
    MAX_PASSWORD_CHARS,
    AdminDailyUpdate,
    AdminLoginRequest,
    AdminMealCreate,
    AdminMealRead,
    AdminMealUpdate,
    ComposeSettingsUpdate,
    QueuedDay,
)
from app.schemas.daily import DailyMealContent
from app.schemas.meal import (
    MAX_CONFIRMED_INGREDIENTS,
    MAX_RECIPE_STEPS,
    MAX_TAGS,
    ProposedIngredient,
)
from app.services.daily_service import DailyService
from app.services.generation_settings_service import GenerationSettingsService
from app.services.ingredient_service import IngredientService
from app.services.meal_edit import UnsafeMealEdit
from app.services.meal_review_service import MealReviewService
from app.services.meal_service import MealService
from app.services.user_service import InvalidCredentials, UserService
from app.web.deps import INGREDIENT_SEPARATOR, ingredient_lines, parse_ingredient_lines, templates

router = APIRouter(prefix="/admin")

# What a moderation button may ask for. Each maps to the JSON router's own handler, so
# the actor stamp and the audit logging keep one implementation.
ModerationAction = Literal["approve", "reject", "delete"]

# A row that either review queue can hand to an edit form.
ReviewedRow = TypeVar("ReviewedRow", CuratedMeal, DailySuggestion)

_REVIEW_STATES = {state.value for state in ApprovalStatus}

# The blank meal form, so a new entry and a rejected one render through the same fields.
_EMPTY_FIELDS = {"name": "", "description": "", "ingredients": "", "recipe": "", "tags": ""}

# Plain copy for the first thing a submitted meal got wrong, keyed by the field the
# schema rejected. Every cap truncates rather than rejects, so only these three can fail.
_FIELD_MESSAGES = {
    "name": "Give the meal a name.",
    "description": "Write a line describing the meal.",
    "ingredients": "List at least one ingredient.",
}


class Refusal(NamedTuple):
    """A rejected meal submission, as its own form re-renders it.

    ``blockers`` are the ingredients the index flagged, formatted by the admin gate, and
    ``can_confirm`` is whether that gate will accept the same submission once the admin
    ticks the confirmation. An unverifiable reading never can.
    """

    message: str
    blockers: list[str]
    can_confirm: bool


class MealForm:
    """The content fields every meal form posts, as one dependency the three writes share.

    The curated create, the curated edit, and the daily edit submit an identical shape:
    ingredients one per line as ``name | category``, the recipe one step per line, tags
    comma separated. Read back here and handed to the admin schemas, whose normalizers do
    the trimming, deduping, and capping — so a page edit can only store what the JSON API
    would have accepted.
    """

    def __init__(
        self,
        name: str = Form(),
        description: str = Form(),
        ingredients: str = Form(),
        recipe: str = Form(default=""),
        tags: str = Form(default=""),
        confirm_flagged: bool = Form(default=False),
    ) -> None:
        self.name = name
        self.description = description
        self.ingredients = ingredients
        self.recipe = recipe
        self.tags = tags
        self.confirm_flagged = confirm_flagged

    def as_payload(self) -> dict[str, object]:
        """The submission shaped for the admin schemas, which normalize every field."""
        return {
            "name": self.name,
            "description": self.description,
            "ingredients": parse_ingredient_lines(self.ingredients),
            "recipe": self.recipe.splitlines(),
            "tags": self.tags.split(","),
            "confirm_flagged": self.confirm_flagged,
        }

    def as_fields(self) -> dict[str, str]:
        """The submission as its own text, so a refused edit is corrected, not retyped."""
        return {
            "name": self.name,
            "description": self.description,
            "ingredients": self.ingredients,
            "recipe": self.recipe,
            "tags": self.tags,
        }


def require_admin_page(user: User | None = Depends(get_current_user_optional)) -> User:
    """The signed-in admin, or a redirect to the panel's own sign-in form.

    A page must not answer a missing session with the API's 401 JSON body. A signed-in
    non-admin lands on the same page, which tells them the account has no admin access.
    """
    if user is None or user.role is not Role.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="Sign in as an admin to do that.",
            headers={"Location": "/admin"},
        )
    return user


@router.get("", response_class=HTMLResponse)
async def panel(
    request: Request,
    review_state: str = Query(default="", alias="status", description="Which curated tab."),
    user: User | None = Depends(get_current_user_optional),
    review: MealReviewService = Depends(get_meal_review_service),
    daily: DailyService = Depends(get_daily_service),
    generation: GenerationSettingsService = Depends(get_generation_settings_service),
) -> HTMLResponse:
    """The whole panel: the composer's settings and triggers, the queue, and the pool."""
    if user is None or user.role is not Role.ADMIN:
        return _sign_in_page(request)
    return await _panel_page(
        request,
        review=review,
        daily=daily,
        generation=generation,
        curated_state=_review_state(review_state),
    )


@router.post("/login")
async def sign_in(
    request: Request,
    email: str = Form(),
    password: str = Form(),
    user_service: UserService = Depends(get_user_service),
) -> Response:
    """Exchange an admin's password for the session cookie the whole site already uses."""
    email = email.strip()
    try:
        payload = AdminLoginRequest(email=email, password=password)
    except ValidationError:
        return _sign_in_page(request, email=email, error="Enter an email and a password.")

    signed_in = RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)
    try:
        user = await user_service.authenticate_admin(payload, ip=client_ip(request))
        set_session_cookie(
            signed_in,
            create_access_token(str(user.id), token_version=user.token_version),
            max_age=settings.session_cookie_max_age,
        )
    except InvalidCredentials as exc:
        # Already worded for the person reading it, and identical for a wrong password,
        # an unknown email, and a disabled account.
        return _sign_in_page(request, email=email, error=str(exc))
    except RateLimitExceeded:
        return _sign_in_page(
            request, email=email, error="Too many attempts. Wait a minute, then try again."
        )
    return signed_in


@router.post("/ui/settings")
async def update_compose_settings(
    request: Request,
    provider: Provider = Form(),
    model: str = Form(default=""),
    admin: User = Depends(require_admin_page),
    generation: GenerationSettingsService = Depends(get_generation_settings_service),
    review: MealReviewService = Depends(get_meal_review_service),
    daily: DailyService = Depends(get_daily_service),
) -> Response:
    """Set which provider and model the composer runs on, here and in the nightly cron."""
    payload = ComposeSettingsUpdate(provider=provider, model=model.strip() or None)
    try:
        await generation.set_composer(payload.provider.value, payload.model, actor=admin.email)
    except LLMError as exc:
        # The choice comes from a list of configured providers, so the one refusal left is
        # OpenRouter, which has no sensible default model and needs one named.
        return await _panel_page(
            request,
            review=review,
            daily=daily,
            generation=generation,
            curated_state=ApprovalStatus.PENDING,
            error=f"That composer setting was refused: {exc}",
        )
    return _back_to("settings")


# Declared before the ``{meal_id}`` route below, which would otherwise claim this path and
# refuse "new" as a malformed id.
@router.get("/ui/meals/new", response_class=HTMLResponse)
async def new_meal_form(
    request: Request, _admin: User = Depends(require_admin_page)
) -> HTMLResponse:
    """The blank form for a hand-written meal, held to the same index gate as a composed one."""
    return _meal_form_page(
        request,
        heading="Write a meal by hand",
        action="/admin/ui/meals",
        fields=_EMPTY_FIELDS,
        meal_types=list(MealType),
    )


@router.post("/ui/meals")
async def create_meal(
    request: Request,
    meal_type: MealType = Form(),
    form: MealForm = Depends(),
    admin: User = Depends(require_admin_page),
    meal_service: MealService = Depends(get_meal_service),
    ingredient_service: IngredientService = Depends(get_ingredient_service),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Store a hand-written meal as pending review, once the index gate lets it through."""
    try:
        payload = AdminMealCreate.model_validate(form.as_payload() | {"meal_type": meal_type})
        await meal_service.create_manual(payload, actor=admin.email, ingredients=ingredient_service)
    except (ValidationError, UnsafeMealEdit) as exc:
        return _meal_form_page(
            request,
            heading="Write a meal by hand",
            action="/admin/ui/meals",
            fields=form.as_fields(),
            meal_types=list(MealType),
            selected_meal_type=meal_type,
            refusal=_refusal(exc),
        )
    return _back_to("curated")


@router.get("/ui/meals/{meal_id}", response_class=HTMLResponse)
async def edit_meal_form(
    request: Request,
    meal_id: UUID,
    _admin: User = Depends(require_admin_page),
    meal_service: MealService = Depends(get_meal_service),
) -> HTMLResponse:
    """The edit form for one pending curated meal."""
    meal = AdminMealRead.model_validate(_editable(await meal_service.get(meal_id)))
    return _meal_form_page(
        request,
        heading=f"Edit {meal.name}",
        subtitle=f"curated pool · {meal.meal_type.value}",
        action=f"/admin/ui/meals/{meal_id}",
        fields=_stored_fields(
            meal.name, meal.description, meal.ingredients, meal.recipe, meal.tags
        ),
    )


@router.post("/ui/meals/{meal_id}")
async def edit_meal(
    request: Request,
    meal_id: UUID,
    form: MealForm = Depends(),
    admin: User = Depends(require_admin_page),
    meal_service: MealService = Depends(get_meal_service),
    ingredient_service: IngredientService = Depends(get_ingredient_service),
) -> Response:
    """Rewrite a pending curated meal, re-checked against the index before it is saved."""
    meal = AdminMealRead.model_validate(_editable(await meal_service.get(meal_id)))
    try:
        payload = AdminMealUpdate.model_validate(form.as_payload())
        await meal_service.edit_pending(meal_id, payload, ingredients=ingredient_service)
    except (ValidationError, UnsafeMealEdit) as exc:
        return _meal_form_page(
            request,
            heading=f"Edit {meal.name}",
            subtitle=f"curated pool · {meal.meal_type.value}",
            action=f"/admin/ui/meals/{meal_id}",
            fields=form.as_fields(),
            refusal=_refusal(exc),
        )
    return _back_to("curated")


@router.post("/ui/meals/{meal_id}/{action}")
async def moderate_meal(
    meal_id: UUID,
    action: ModerationAction,
    review_state: str = Form(default="", alias="status"),
    admin: User = Depends(require_admin_page),
    service: MealReviewService = Depends(get_meal_review_service),
) -> Response:
    """Approve, reject, or permanently remove one curated meal."""
    # A row that went while the panel sat open is a no-op; the reloaded list is the
    # honest answer either way.
    if action == "approve":
        await service.approve(meal_id, actor=admin.email)
    elif action == "reject":
        await service.reject(meal_id)
    else:
        await service.delete(meal_id, actor=admin.email)
    return _back_to("curated", review_state=_review_state(review_state))


@router.get("/ui/daily/{suggestion_id}", response_class=HTMLResponse)
async def edit_suggestion_form(
    request: Request,
    suggestion_id: UUID,
    _admin: User = Depends(require_admin_page),
    daily: DailyService = Depends(get_daily_service),
) -> HTMLResponse:
    """The edit form for one pending daily slot."""
    suggestion = _editable(await daily.get(suggestion_id))
    content = DailyMealContent.model_validate(suggestion.content)
    return _meal_form_page(
        request,
        heading=f"Edit {content.name}",
        subtitle=f"{suggestion.suggestion_date} · {suggestion.meal_type.value}",
        action=f"/admin/ui/daily/{suggestion_id}",
        fields=_stored_fields(
            content.name, content.description, content.ingredients, content.recipe, content.tags
        ),
    )


@router.post("/ui/daily/{suggestion_id}")
async def edit_suggestion(
    request: Request,
    suggestion_id: UUID,
    form: MealForm = Depends(),
    admin: User = Depends(require_admin_page),
    daily: DailyService = Depends(get_daily_service),
    ingredient_service: IngredientService = Depends(get_ingredient_service),
) -> Response:
    """Rewrite a pending daily slot, re-checked against the index before it is saved."""
    suggestion = _editable(await daily.get(suggestion_id))
    try:
        payload = AdminDailyUpdate.model_validate(form.as_payload())
        await daily.edit_pending(suggestion_id, payload, ingredients=ingredient_service)
    except (ValidationError, UnsafeMealEdit) as exc:
        return _meal_form_page(
            request,
            heading=f"Edit {DailyMealContent.model_validate(suggestion.content).name}",
            subtitle=f"{suggestion.suggestion_date} · {suggestion.meal_type.value}",
            action=f"/admin/ui/daily/{suggestion_id}",
            fields=form.as_fields(),
            refusal=_refusal(exc),
        )
    return _back_to("queue")


@router.post("/ui/daily/{suggestion_id}/{action}")
async def moderate_suggestion(
    suggestion_id: UUID,
    action: ModerationAction,
    admin: User = Depends(require_admin_page),
    daily: DailyService = Depends(get_daily_service),
) -> Response:
    """Approve, reject, or permanently remove one daily slot."""
    # A slot that went while the panel sat open is a no-op, as above.
    if action == "approve":
        await daily.approve(suggestion_id, actor=admin.email)
    elif action == "reject":
        await daily.reject(suggestion_id)
    else:
        await daily.delete(suggestion_id, actor=admin.email)
    return _back_to("queue")


async def _panel_page(
    request: Request,
    *,
    review: MealReviewService,
    daily: DailyService,
    generation: GenerationSettingsService,
    curated_state: ApprovalStatus,
    error: str | None = None,
) -> HTMLResponse:
    """Render the panel: the composer's settings and triggers, the queue, and one pool tab."""
    today = datetime.now(UTC).date()
    queue = await daily.list_queue(today=today)
    stored = await generation.get()
    rows = await review.list_by_status(curated_state)
    return _private(
        templates.TemplateResponse(
            request,
            "admin.html",
            {
                "meals": [AdminMealRead.model_validate(row) for row in rows],
                "curated_state": curated_state,
                "review_states": list(ApprovalStatus),
                "queue": queue,
                "meal_types": list(MealType),
                "provider": stored.composer_provider,
                "model": stored.composer_model,
                "available_providers": selectable_providers(),
                "today": today,
                "latest_date": today + timedelta(days=settings.daily_queue_max_ahead_days),
                "compose_date": _default_compose_date(queue, today),
                "error": error,
            },
        )
    )


def _sign_in_page(request: Request, *, email: str = "", error: str | None = None) -> HTMLResponse:
    """The password form, or the note that this account has no admin access."""
    return _private(
        templates.TemplateResponse(
            request,
            "admin_login.html",
            {
                "email": email,
                "error": error,
                "max_email_chars": MAX_EMAIL_CHARS,
                "max_password_chars": MAX_PASSWORD_CHARS,
            },
        )
    )


def _meal_form_page(
    request: Request,
    *,
    heading: str,
    action: str,
    fields: dict[str, str],
    subtitle: str = "",
    meal_types: list[MealType] | None = None,
    selected_meal_type: MealType | None = None,
    refusal: Refusal | None = None,
) -> HTMLResponse:
    """One meal's fields as a page of their own: a new entry, or an edit of a pending row.

    ``meal_types`` is set only for a new meal, the one submission that picks a slot; an
    edit keeps the slot its meal was composed for.
    """
    return _private(
        templates.TemplateResponse(
            request,
            "admin_meal_form.html",
            {
                "heading": heading,
                "subtitle": subtitle,
                "action": action,
                "fields": fields,
                "meal_types": meal_types,
                "selected_meal_type": selected_meal_type,
                "refusal": refusal,
                "separator": INGREDIENT_SEPARATOR,
                "max_ingredients": MAX_CONFIRMED_INGREDIENTS,
                "max_recipe_steps": MAX_RECIPE_STEPS,
                "max_tags": MAX_TAGS,
            },
        )
    )


def _stored_fields(
    name: str,
    description: str,
    ingredients: list[ProposedIngredient],
    recipe: list[str] | None,
    tags: list[str],
) -> dict[str, str]:
    """A stored meal as the form's text, in the shape the submission is read back from."""
    return {
        "name": name,
        "description": description,
        "ingredients": ingredient_lines(ingredients),
        "recipe": "\n".join(recipe or []),
        "tags": ", ".join(tags),
    }


def _editable(row: ReviewedRow | None) -> ReviewedRow:
    """A row an edit form may open, or a redirect back to the panel.

    Missing and already-decided answer the same way: the panel offers an edit link only
    while a row is pending, so either case is a hand-typed URL, and the panel is the page
    that shows how things actually stand.
    """
    if row is None or row.approval_status is not ApprovalStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="Only a pending meal can be edited.",
            headers={"Location": "/admin"},
        )
    return row


def _refusal(exc: Exception) -> Refusal:
    """A rejected submission as the copy its form shows.

    The admin gate's 422 carries the flagged ingredients and whether they can be confirmed
    past; every other refusal is a single sentence.
    """
    if isinstance(exc, ValidationError):
        location = exc.errors()[0]["loc"]
        field = str(location[0]) if location else ""
        fallback = "That meal couldn't be saved. Check the fields and retry."
        return Refusal(_FIELD_MESSAGES.get(field, fallback), [], False)
    if isinstance(exc, UnsafeMealEdit):
        return Refusal(exc.message, exc.blockers, exc.can_confirm)
    return Refusal(str(getattr(exc, "detail", exc)), [], False)


def _default_compose_date(queue: list[QueuedDay], today: date) -> date:
    """The date a new daily composition most likely targets.

    The first upcoming day still missing a slot, else the day after the last queued one so
    working further ahead does not land on a full date, kept inside the window the compose
    route accepts.
    """
    incomplete = next((day for day in queue if day.missing_meal_types), None)
    if incomplete is not None:
        return incomplete.date
    latest = today + timedelta(days=settings.daily_queue_max_ahead_days)
    return min(queue[-1].date + timedelta(days=1), latest) if queue else today


def _review_state(value: str) -> ApprovalStatus:
    """The requested curated tab; anything unrecognised falls back to the review queue."""
    return ApprovalStatus(value) if value in _REVIEW_STATES else ApprovalStatus.PENDING


def _back_to(anchor: str, *, review_state: ApprovalStatus | None = None) -> RedirectResponse:
    """Redirect to the panel section a write came from, so a reload cannot repeat it."""
    query = f"?status={review_state.value}" if review_state else ""
    return RedirectResponse(f"/admin{query}#{anchor}", status_code=status.HTTP_303_SEE_OTHER)


def _private(response: HTMLResponse) -> HTMLResponse:
    """Mark a page as privileged, so nothing between here and the browser keeps it."""
    response.headers["Cache-Control"] = "no-store"
    return response
