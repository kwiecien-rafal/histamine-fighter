"""Endpoint tests for the admin compose router: save, settings, and the queue.

The streaming routes are driven with a scripted fake streamer so no model runs. For the
save paths the fake runs the route's persist callback on the rolled-back test session
(flush, not commit), so a stored row stays inside the test transaction and is cleaned up
after, exactly as the real streamer would write it on its own session.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.composer import ComposerExhausted
from app.api.admin import compose
from app.config import settings
from app.core.ratelimit import limiter
from app.core.security import hash_password
from app.db.session import get_session
from app.dependencies import get_composer_streamer
from app.embeddings import get_embedder
from app.enums import ApprovalStatus, MealType, Role
from app.llm.errors import LLMInvocationError
from app.main import create_app
from app.models import CuratedMeal, DailySuggestion
from app.models.user import User
from app.schemas.meal import (
    ComposedMeal,
    MealStreamItem,
    ProposedIngredient,
    SavedEvent,
    TraceEvent,
)
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD
from tests.fakes import FakeEmbedder

_TOMORROW = (datetime.now(UTC) + timedelta(days=1)).date()


def _meal(
    meal_type: MealType = MealType.LUNCH, *, name: str = "Courgette ribbon salad"
) -> ComposedMeal:
    return ComposedMeal(
        name=name,
        meal_type=meal_type,
        description="raw courgette ribbons with olive oil and fresh herbs",
        ingredients=[ProposedIngredient(name="courgette", category="vegetable")],
        recipe=["Peel into ribbons.", "Toss with oil and herbs."],
        tags=["fresh"],
        unverified_ingredients=[],
        model="fake/test",
        reasoning_trace=[
            TraceEvent(kind="reject", text="Dropped parmesan: avoid.", ingredient="parmesan"),
            TraceEvent(kind="verify", text="Courgette cleared the index."),
        ],
    )


class _ScriptedStreamer:
    """A scripted compose stream: each trace step, the meal, then the save confirmation.

    When the route passes a persist callback, it runs on the supplied test session so the
    write lands in the test transaction, mirroring the real streamer without a model call.
    """

    def __init__(self, meal: ComposedMeal, *, session: AsyncSession | None = None) -> None:
        self._meal = meal
        self._session = session

    async def stream(
        self, meal_type: MealType, *, persist: Any = None
    ) -> AsyncIterator[dict[str, str]]:
        for event in self._meal.reasoning_trace:
            yield {"event": "trace", "data": event.model_dump_json()}
        yield {"event": "meal", "data": MealStreamItem.of(self._meal).meal.model_dump_json()}
        if persist is not None:
            assert self._session is not None
            saved_id = await persist(self._meal, self._session)
            yield {"event": "saved", "data": SavedEvent(id=saved_id).model_dump_json()}


class _RaisingStreamer:
    """Streams one trace step, then raises, to exercise the route's terminal-error backstop.

    The 200 and headers are already sent by the time the failure lands, so the route cannot
    turn it into an HTTP error: it has to close the open stream as an ``error`` frame.
    """

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def stream(
        self, meal_type: MealType, *, persist: Any = None
    ) -> AsyncIterator[dict[str, str]]:
        yield {
            "event": "trace",
            "data": TraceEvent(kind="check", text="Checking courgette.").model_dump_json(),
        }
        raise self._error


@asynccontextmanager
async def _compose_client(session: AsyncSession, streamer: object) -> AsyncIterator[AsyncClient]:
    """An authenticated admin client whose composer is the given scripted fake."""
    app = create_app()

    async def _use_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    app.dependency_overrides[get_composer_streamer] = lambda: streamer
    # The curated route embeds through the real singleton; the deterministic fake keeps
    # tests off the ONNX model and decoupled from which route happens to embed.
    app.dependency_overrides[get_embedder] = lambda: FakeEmbedder()
    limiter.enabled = False
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            login = await http_client.post(
                "/admin/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
            )
            assert login.status_code == 200
            yield http_client
    finally:
        limiter.enabled = True


async def _add_daily(
    session: AsyncSession,
    *,
    meal_type: MealType = MealType.LUNCH,
    on: date = _TOMORROW,
    approval_status: ApprovalStatus = ApprovalStatus.APPROVED,
    name: str = "Old salad",
) -> DailySuggestion:
    row = DailySuggestion(
        suggestion_date=on,
        meal_type=meal_type,
        content={
            "name": name,
            "description": "",
            "ingredients": [],
            "recipe": None,
            "tags": [],
            "unverified_ingredients": [],
        },
        model="old/model",
        reasoning_trace=[],
        reveal_at=datetime(on.year, on.month, on.day, 10, tzinfo=UTC),
        approval_status=approval_status,
    )
    session.add(row)
    await session.flush()
    return row


# --- compose streaming: shared route behavior -------------------------------------
# These exercise the route-level guards (validation, the in-flight lock, the terminal
# error backstop) shared by every compose stream, driven through the daily route as a
# representative example; the scripted streamer means no model ever runs.


async def test_compose_with_a_bad_meal_type_is_422(session: AsyncSession, admin_user: User) -> None:
    async with _compose_client(session, _ScriptedStreamer(_meal())) as client:
        resp = await client.post("/admin/compose/curated", json={"meal_type": "brunch"})

    assert resp.status_code == 422


async def test_compose_while_a_run_is_in_flight_is_409(
    session: AsyncSession, admin_user: User
) -> None:
    await compose._compose_lock.acquire()
    try:
        async with _compose_client(session, _ScriptedStreamer(_meal())) as client:
            resp = await client.post(
                "/admin/compose/daily",
                json={"meal_type": "lunch", "date": _TOMORROW.isoformat()},
            )
    finally:
        compose._compose_lock.release()

    assert resp.status_code == 409
    assert "already running" in resp.json()["detail"]


async def test_compose_exhausted_closes_as_an_error_event(
    session: AsyncSession, admin_user: User
) -> None:
    streamer = _RaisingStreamer(ComposerExhausted("the loop spent its budget"))
    async with _compose_client(session, streamer) as client:
        resp = await client.post(
            "/admin/compose/daily", json={"meal_type": "lunch", "date": _TOMORROW.isoformat()}
        )

    assert resp.status_code == 200
    body = resp.text
    assert "event: trace" in body
    assert "event: error" in body
    assert "could not finish a safe meal" in body


async def test_compose_llm_error_surfaces_its_message(
    session: AsyncSession, admin_user: User
) -> None:
    streamer = _RaisingStreamer(LLMInvocationError("The model cannot call tools."))
    async with _compose_client(session, streamer) as client:
        resp = await client.post(
            "/admin/compose/daily", json={"meal_type": "lunch", "date": _TOMORROW.isoformat()}
        )

    assert resp.status_code == 200
    body = resp.text
    assert "event: error" in body
    # A model failure is already user-safe, so its own message reaches the client.
    assert "The model cannot call tools." in body


async def test_compose_unexpected_failure_closes_as_an_error_event(
    session: AsyncSession, admin_user: User
) -> None:
    streamer = _RaisingStreamer(RuntimeError("boom: the database connection dropped"))
    async with _compose_client(session, streamer) as client:
        resp = await client.post(
            "/admin/compose/daily", json={"meal_type": "lunch", "date": _TOMORROW.isoformat()}
        )

    assert resp.status_code == 200
    body = resp.text
    assert "event: error" in body
    assert "Something went wrong" in body
    # The raw exception detail stays in the server log, never the response.
    assert "boom" not in body


# --- POST /admin/compose/curated --------------------------------------------------


async def test_curated_save_inserts_one_pending_row_with_trace_and_embedding(
    session: AsyncSession, admin_user: User
) -> None:
    streamer = _ScriptedStreamer(_meal(), session=session)

    async with _compose_client(session, streamer) as client:
        resp = await client.post("/admin/compose/curated", json={"meal_type": "lunch"})

    assert resp.status_code == 200
    body = resp.text
    assert "event: trace" in body
    assert "Dropped parmesan" in body  # the trace streams to the client as it runs
    assert "event: meal" in body
    assert "event: saved" in body

    rows = (await session.execute(select(CuratedMeal))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.approval_status is ApprovalStatus.PENDING
    assert row.name == "Courgette ribbon salad"
    assert [event["text"] for event in row.reasoning_trace] == [
        "Dropped parmesan: avoid.",
        "Courgette cleared the index.",
    ]
    assert len(row.embedding) > 0  # embedded for later similarity retrieval


# --- POST /admin/compose/daily ----------------------------------------------------


async def test_daily_save_inserts_a_pending_row_with_trace(
    session: AsyncSession, admin_user: User
) -> None:
    streamer = _ScriptedStreamer(_meal(), session=session)

    async with _compose_client(session, streamer) as client:
        resp = await client.post(
            "/admin/compose/daily",
            json={"meal_type": "lunch", "date": _TOMORROW.isoformat()},
        )

    assert resp.status_code == 200
    assert "event: saved" in resp.text

    row = (
        await session.execute(
            select(DailySuggestion).where(DailySuggestion.suggestion_date == _TOMORROW)
        )
    ).scalar_one()
    assert row.approval_status is ApprovalStatus.PENDING
    assert row.content["name"] == "Courgette ribbon salad"
    assert row.reasoning_trace[0]["kind"] == "reject"


async def test_daily_save_conflicts_when_slot_is_taken_and_not_replacing(
    session: AsyncSession, admin_user: User
) -> None:
    await _add_daily(session, approval_status=ApprovalStatus.APPROVED)
    streamer = _ScriptedStreamer(_meal(), session=session)

    async with _compose_client(session, streamer) as client:
        resp = await client.post(
            "/admin/compose/daily",
            json={"meal_type": "lunch", "date": _TOMORROW.isoformat()},
        )

    assert resp.status_code == 409
    conflict = resp.json()["detail"]["conflict"]
    assert conflict["existing_status"] == "approved"
    assert conflict["meal_type"] == "lunch"
    # The refusal happened before composing, so nothing was overwritten.
    row = (
        await session.execute(
            select(DailySuggestion).where(DailySuggestion.suggestion_date == _TOMORROW)
        )
    ).scalar_one()
    assert row.content["name"] == "Old salad"


async def test_daily_save_with_replace_overwrites_the_slot(
    session: AsyncSession, admin_user: User
) -> None:
    await _add_daily(session, approval_status=ApprovalStatus.APPROVED)
    streamer = _ScriptedStreamer(_meal(name="Fresh lunch"), session=session)

    async with _compose_client(session, streamer) as client:
        resp = await client.post(
            "/admin/compose/daily",
            json={"meal_type": "lunch", "date": _TOMORROW.isoformat(), "replace": True},
        )

    assert resp.status_code == 200
    assert "event: saved" in resp.text
    rows = (
        (
            await session.execute(
                select(DailySuggestion).where(DailySuggestion.suggestion_date == _TOMORROW)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1  # overwritten in place, not duplicated
    assert rows[0].content["name"] == "Fresh lunch"
    assert rows[0].approval_status is ApprovalStatus.PENDING  # re-pended for review


async def test_daily_save_with_a_date_past_the_window_is_422(
    session: AsyncSession, admin_user: User
) -> None:
    far = datetime.now(UTC).date() + timedelta(days=settings.daily_queue_max_ahead_days + 1)
    streamer = _ScriptedStreamer(_meal(), session=session)

    async with _compose_client(session, streamer) as client:
        resp = await client.post(
            "/admin/compose/daily",
            json={"meal_type": "lunch", "date": far.isoformat()},
        )

    assert resp.status_code == 422


# --- GET/PUT /admin/compose/settings ----------------------------------------------


@pytest.fixture
def _known_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deterministic provider environment: only OpenAI keyed, self-hosted."""
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(settings, "gemini_api_key", None)
    monkeypatch.setattr(settings, "openrouter_api_key", None)
    monkeypatch.setattr(settings, "public_deployment", False)
    monkeypatch.setattr(settings, "llm_provider", "ollama")


async def test_get_settings_reports_current_and_available(
    authenticated_client: AsyncClient, _known_keys: None
) -> None:
    resp = await authenticated_client.get("/admin/compose/settings")

    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "ollama"  # the default, nothing saved yet
    assert body["model"] is None
    assert set(body["available_providers"]) == {"openai", "ollama"}


async def test_put_settings_saves_a_keyed_provider(
    authenticated_client: AsyncClient, _known_keys: None
) -> None:
    resp = await authenticated_client.put(
        "/admin/compose/settings", json={"provider": "openai", "model": "gpt-5.4-mini"}
    )

    assert resp.status_code == 200
    assert resp.json()["provider"] == "openai"
    assert resp.json()["model"] == "gpt-5.4-mini"

    after = await authenticated_client.get("/admin/compose/settings")
    assert after.json()["provider"] == "openai"
    assert after.json()["model"] == "gpt-5.4-mini"


async def test_put_settings_rejects_a_keyless_provider(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "anthropic_api_key", None)

    resp = await authenticated_client.put(
        "/admin/compose/settings", json={"provider": "anthropic", "model": "claude-sonnet-4-6"}
    )

    assert resp.status_code == 400


# --- GET /admin/daily/queue -------------------------------------------------------


async def test_queue_groups_upcoming_days_with_counts_and_gaps(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    today = datetime.now(UTC).date()
    await _add_daily(
        session, on=today, meal_type=MealType.BREAKFAST, approval_status=ApprovalStatus.APPROVED
    )
    await _add_daily(
        session, on=today, meal_type=MealType.LUNCH, approval_status=ApprovalStatus.PENDING
    )
    await _add_daily(
        session, on=_TOMORROW, meal_type=MealType.DINNER, approval_status=ApprovalStatus.PENDING
    )

    resp = await authenticated_client.get("/admin/daily/queue")

    assert resp.status_code == 200
    days = resp.json()
    assert [day["date"] for day in days] == [today.isoformat(), _TOMORROW.isoformat()]
    first = days[0]
    assert [slot["meal_type"] for slot in first["slots"]] == ["breakfast", "lunch"]
    assert first["approved_count"] == 1
    assert first["pending_count"] == 1
    assert set(first["missing_meal_types"]) == {"dinner", "snack"}


# --- auth gate --------------------------------------------------------------------


@pytest_asyncio.fixture
async def non_admin_client(client: AsyncClient, session: AsyncSession) -> AsyncClient:
    """A signed-in non-admin user, for the 403 (authenticated but not allowed) cases."""
    session.add(
        User(email="user@example.com", password_hash=hash_password("secret123"), role=Role.USER)
    )
    await session.flush()
    resp = await client.post(
        "/admin/auth/login", json={"email": "user@example.com", "password": "secret123"}
    )
    assert resp.status_code == 200
    return client


_PROTECTED = [
    ("get", "/admin/compose/settings", None),
    ("put", "/admin/compose/settings", {"provider": "openai", "model": "gpt-5.4-mini"}),
    ("post", "/admin/compose/curated", {"meal_type": "lunch"}),
    ("post", "/admin/compose/daily", {"meal_type": "lunch", "date": _TOMORROW.isoformat()}),
    ("get", "/admin/daily/queue", None),
]


@pytest.mark.parametrize(("method", "path", "body"), _PROTECTED)
async def test_routes_reject_anonymous(
    client: AsyncClient, method: str, path: str, body: dict[str, Any] | None
) -> None:
    resp = await client.request(method, path, json=body)
    assert resp.status_code == 401


@pytest.mark.parametrize(("method", "path", "body"), _PROTECTED)
async def test_routes_reject_a_non_admin(
    non_admin_client: AsyncClient, method: str, path: str, body: dict[str, Any] | None
) -> None:
    resp = await non_admin_client.request(method, path, json=body)
    assert resp.status_code == 403
