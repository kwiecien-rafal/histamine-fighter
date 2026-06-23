"""Tests for user account creation, password reset, and authentication."""

from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.enums import Role
from app.models.user import User
from app.services.user_service import UserService

_EMAIL = "admin@example.com"
_PASSWORD = "supersecret"


async def test_create_then_authenticate(session: AsyncSession) -> None:
    service = UserService(session)

    user, created = await service.create_or_update(_EMAIL, _PASSWORD)

    assert created is True
    assert await service.authenticate(_EMAIL, _PASSWORD) is user
    assert await service.authenticate(_EMAIL, "wrong") is None


async def test_create_stamps_the_admin_role(session: AsyncSession) -> None:
    # create_or_update is the admin-elevation path (the create_admin CLI), so a new
    # account is an admin, not the least-privilege default.
    user, _ = await UserService(session).create_or_update(_EMAIL, _PASSWORD)

    assert user.role is Role.ADMIN


async def test_create_or_update_elevates_an_existing_account_to_admin(
    session: AsyncSession,
) -> None:
    # Running the CLI for an existing non-admin email grants admin, so the elevation
    # path does not silently leave a user un-elevated.
    existing = User(email=_EMAIL, password_hash=hash_password("old-password"), role=Role.USER)
    session.add(existing)
    await session.flush()

    user, created = await UserService(session).create_or_update(_EMAIL, _PASSWORD)

    assert created is False
    assert user is existing
    assert user.role is Role.ADMIN


async def test_get_by_id_returns_the_account(session: AsyncSession) -> None:
    service = UserService(session)
    user, _ = await service.create_or_update(_EMAIL, _PASSWORD)
    await session.flush()

    assert await service.get_by_id(user.id) is user


async def test_get_by_id_unknown_returns_none(session: AsyncSession) -> None:
    assert await UserService(session).get_by_id(uuid4()) is None


async def test_set_active_disables_then_reenables_an_account(session: AsyncSession) -> None:
    service = UserService(session)
    user, _ = await service.create_or_update(_EMAIL, _PASSWORD)

    assert await service.set_active(_EMAIL, active=False) is user
    assert user.is_active is False
    assert await service.set_active(_EMAIL, active=True) is user
    assert user.is_active is True


async def test_set_active_unknown_email_returns_none(session: AsyncSession) -> None:
    assert await UserService(session).set_active("ghost@example.com", active=False) is None


async def test_create_or_update_resets_an_existing_password(session: AsyncSession) -> None:
    service = UserService(session)
    await service.create_or_update(_EMAIL, _PASSWORD)

    user, created = await service.create_or_update(_EMAIL, "a-new-password")

    assert created is False
    assert user.email == _EMAIL
    assert await service.authenticate(_EMAIL, "a-new-password") is user
    assert await service.authenticate(_EMAIL, _PASSWORD) is None


async def test_password_reset_bumps_the_token_version(session: AsyncSession) -> None:
    service = UserService(session)
    user, _ = await service.create_or_update(_EMAIL, _PASSWORD)
    await session.flush()
    assert user.token_version == 1

    await service.create_or_update(_EMAIL, "a-new-password")

    assert user.token_version == 2


async def test_email_is_normalized_on_create_and_lookup(session: AsyncSession) -> None:
    service = UserService(session)
    await service.create_or_update("Admin@Example.COM", _PASSWORD)

    assert await service.get_by_email("admin@example.com") is not None
    # A second create under different casing resets, never duplicates, the account.
    _, created = await service.create_or_update("ADMIN@example.com", "another-one")
    assert created is False


async def test_authenticate_unknown_email_returns_none(session: AsyncSession) -> None:
    assert await UserService(session).authenticate("ghost@example.com", _PASSWORD) is None
