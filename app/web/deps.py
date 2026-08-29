"""Template plumbing, session wiring, and form parsing shared by the pages.

One :class:`Jinja2Templates` instance for the whole app, plus the display filters that
are awkward to express in a template. Formatting and branded wording live here and in
the templates only — never in the domain values the API and the database exchange
(CLAUDE section 19).

The signed-in account is resolved once per request by ``bind_current_user`` and handed
to every template by a context processor, so the masthead can render the account slot
without each page passing it through.

The ingredient editor is the one form shape two pages share — the saved copy and the
dish lookup both let a visitor rewrite an ingredient list — so its line format is
written and read here rather than in each of them.
"""

from datetime import UTC, date, datetime
from pathlib import Path

from fastapi import Depends, HTTPException, Request, status
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.dependencies import get_current_user_optional
from app.llm.providers import DEFAULT_MODELS
from app.models.user import User
from app.schemas.meal import ProposedIngredient
from app.services.meal_service import MANUAL_MODEL

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = _PACKAGE_ROOT / "templates"
STATIC_DIR = _PACKAGE_ROOT / "static"


async def bind_current_user(
    request: Request, user: User | None = Depends(get_current_user_optional)
) -> None:
    """Stash the request's user where the template context processor can find it.

    Declared once on the web router rather than by each page: the shell shows the
    account on every page, so no handler should have to ask for it.
    """
    request.state.user = user


def require_user(user: User | None = Depends(get_current_user_optional)) -> User:
    """The signed-in user, or a redirect to the sign-in page.

    A page must not answer a missing session with the API's 401 JSON body; the
    browser is sent to sign in instead. FastAPI caches the resolved dependency, so
    a page using this shares the lookup ``bind_current_user`` already made.
    """
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="Sign in to see that page.",
            headers={"Location": "/login"},
        )
    return user


def current_path(request: Request) -> str:
    """This page's own path and query, for the return-here field on its forms."""
    return f"{request.url.path}?{request.url.query}" if request.url.query else request.url.path


def safe_redirect(target: str, fallback: str) -> str:
    """A return-here path supplied by a form, accepted only if it stays on this site.

    The pages carry where to come back to in a hidden field (``Referrer-Policy:
    no-referrer`` means there is no header to read it from), so the value is
    attacker-supplied and anything that could leave the site is refused.
    """
    return target if target.startswith("/") and not target.startswith("//") else fallback


def _session_context(request: Request) -> dict[str, object]:
    """Expose the signed-in user and the deployment mode to every template.

    ``user`` is ``None`` off the web router. ``public_deployment`` gates the choices
    the AI panel may offer — Ollama is a self-hosted option only — and is read per
    request so a test that flips the setting sees the panel change with it.
    """
    return {
        "user": getattr(request.state, "user", None),
        "public_deployment": settings.public_deployment,
    }


templates = Jinja2Templates(directory=TEMPLATES_DIR, context_processors=[_session_context])

# The hand-authored sentinel is read by the attribution macro. Exposed from the service
# rather than re-typed in a template, so the literal has exactly one definition.
templates.env.globals["MANUAL_MODEL"] = MANUAL_MODEL

# The AI panel shows each provider's default model as a placeholder. Taken from the
# resolver rather than re-typed as copy, so the hint cannot drift from what is used.
templates.env.globals["DEFAULT_MODELS"] = DEFAULT_MODELS


def board_date(value: date) -> str:
    """A board's calendar date, as 'Friday, 28 August 2026'."""
    return value.strftime("%A, %d %B %Y")


def utc_time(value: datetime) -> str:
    """A reveal time in UTC, as '10:00 UTC' — the same wall clock for every visitor."""
    return value.astimezone(UTC).strftime("%H:%M UTC")


def countdown(target: datetime) -> str:
    """Roughly how long until a target, as '3h 05m', '12m', or 'any moment now'.

    Rendered once per request rather than ticked by a script: the visitor reloads to
    see it move, which is the whole cost of not shipping a countdown widget.
    """
    minutes_left = int((target - datetime.now(UTC)).total_seconds() // 60)
    if minutes_left < 1:
        return "any moment now"
    hours, minutes = divmod(minutes_left, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


templates.env.filters["board_date"] = board_date
templates.env.filters["utc_time"] = utc_time
templates.env.filters["countdown"] = countdown


# What separates an ingredient's name from its category on a line of the editor.
INGREDIENT_SEPARATOR = "|"


def ingredient_lines(ingredients: list[ProposedIngredient]) -> str:
    """An ingredient list as editable text, one ``name | category`` per line."""
    return "\n".join(
        f"{item.name} {INGREDIENT_SEPARATOR} {item.category}" if item.category else item.name
        for item in ingredients
    )


def parse_ingredient_lines(text: str) -> list[dict[str, str]]:
    """Read the editor's lines back; the category after the separator is optional.

    Blank lines are dropped here and the schema each caller validates against does
    the trimming, deduping, and capping, so a page edit can only produce what that
    schema would accept from the API.
    """
    parsed: list[dict[str, str]] = []
    for line in text.splitlines():
        name, _, category = line.partition(INGREDIENT_SEPARATOR)
        if name.strip():
            parsed.append({"name": name, "category": category})
    return parsed
