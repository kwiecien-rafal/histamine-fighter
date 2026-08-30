"""Template plumbing, session wiring, and form parsing shared by the pages.

One :class:`Jinja2Templates` instance for the whole app, plus the display filters that
are awkward to express in a template. Formatting and branded wording live here and in
the templates only — never in the domain values the API and the database exchange
(CLAUDE section 19).

The signed-in account is resolved once per request by ``bind_current_user`` and handed
to every template by a context processor, so the masthead can render the account slot
without each page passing it through.

The dish lookup's swap advice is joined together here for the same reason: which
role a change carries is a name-membership test between two lists, which a template
expresses badly.

The ingredient editor is the one form shape three pages share — the dish lookup, the
saved copy, and the admin meal form all let someone rewrite an ingredient list — so
the rows are read back and their categories re-attached here rather than in each of
them.
"""

import json
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import NamedTuple

from fastapi import Depends, HTTPException, Request, status
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.dependencies import get_current_user_optional
from app.enums import AdaptationAction, CulinaryRole, RewriteOutcome
from app.llm.providers import DEFAULT_MODELS
from app.models.user import User
from app.schemas.meal import (
    MAX_CONFIRMED_INGREDIENTS,
    MAX_INGREDIENT_CHARS,
    AdaptedDish,
    DishAssessmentResponse,
    ProposedIngredient,
    normalize_ingredients,
)
from app.services.meal_service import MANUAL_MODEL

_WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = _WEB_DIR / "templates"
STATIC_DIR = _WEB_DIR / "static"


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

# The editor macro enforces its own limits rather than having every route that renders
# it pass the same two caps through its template context.
templates.env.globals["MAX_INGREDIENTS"] = MAX_CONFIRMED_INGREDIENTS
templates.env.globals["MAX_INGREDIENT_CHARS"] = MAX_INGREDIENT_CHARS


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


class SwapRow(NamedTuple):
    """One line of the lookup card's advice: what to do about one problem ingredient.

    ``kept`` is the honest row — nothing in the index replaces these and the dish
    holds on to them anyway, so it reads as advice rather than as a fix.
    """

    ingredients: list[str]
    replacement: str | None
    role: CulinaryRole | None
    reason: str
    kept: bool


def swap_rows(result: DishAssessmentResponse, adapted: AdaptedDish) -> list[SwapRow]:
    """The swap advice for whichever dish the card is showing.

    A rewrite the index cleared has its own diff, so the rows are its changes and
    the role is joined back on from the assessment entry that covers the ingredient.
    Every other outcome has no new dish to diff, so the rows are the assessment's
    own adaptations — advice on the dish as named, with the ingredients nothing
    replaces marked as staying.
    """
    if adapted.outcome is not RewriteOutcome.ADAPTED:
        return [
            SwapRow(
                ingredients=entry.ingredients,
                replacement=entry.swap,
                role=entry.role,
                reason=entry.reason,
                kept=entry.action is AdaptationAction.NO_SAFE_SWAP,
            )
            for entry in result.adaptations
        ]
    roles = {
        name.casefold(): entry.role for entry in result.adaptations for name in entry.ingredients
    }
    return [
        SwapRow(
            ingredients=[change.original],
            replacement=change.replacement,
            role=roles.get(change.original.casefold()),
            reason=change.reason,
            kept=False,
        )
        for change in adapted.changes
    ]


# An ingredient's category never appears in the editor: it is a matching hint for the
# curated index ("aged hard cheese" catches parmesan, which the index holds only as an
# umbrella row), not something a person should have to write or vet. So the rows carry
# names alone, and the categories the page was rendered with ride along beside them to
# be re-attached to whatever comes back unchanged.

# Bounds on that map, derived from the row limit so they can never be what refuses a
# legitimate list. A map past either one is discarded whole rather than truncated to an
# arbitrary half. The character bound is checked before parsing, so an oversized field
# is dropped rather than decoded.
MAX_KNOWN_CATEGORIES = MAX_CONFIRMED_INGREDIENTS * 2
MAX_KNOWN_CHARS = MAX_KNOWN_CATEGORIES * (MAX_INGREDIENT_CHARS * 2 + 8)


def known_categories(ingredients: Iterable[ProposedIngredient]) -> str:
    """The rendered rows' categories, keyed by name, as the editor's hidden field."""
    known = {item.name.casefold(): item.category for item in ingredients if item.category}
    return json.dumps(known, separators=(",", ":")) if known else ""


def read_known_categories(raw: str) -> dict[str, str]:
    """Read that map back, degrading anything unreadable to no categories at all.

    On the lookup path the map round-trips through the browser, so it is parsed as
    hostile input. Losing it is safe in one direction only, which is why this may
    give up on the whole map: a category can only widen the search to an umbrella
    row, and a name that matches nothing already reads as no known concern, so the
    worst an empty map costs is grounding — never caution.
    """
    if not raw or len(raw) > MAX_KNOWN_CHARS:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(parsed, dict) or len(parsed) > MAX_KNOWN_CATEGORIES:
        return {}
    return {
        name: category
        for name, category in parsed.items()
        if isinstance(name, str) and isinstance(category, str)
    }


def confirmed_ingredients(
    names: Iterable[str], known: Mapping[str, str]
) -> list[ProposedIngredient]:
    """The editor's rows as a normalized list, categories kept only for unchanged names.

    A row that was renamed, typed by hand, or split out of another matches nothing
    in the map and so carries no category. That is the whole guard against a stale
    descriptor: it is not that a wrong category is detected, it is that an edited
    name cannot carry one at all.
    """
    return normalize_ingredients((name, known.get(name.strip().casefold())) for name in names)
