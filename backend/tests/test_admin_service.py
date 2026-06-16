"""Tests for admin account creation, password reset, and authentication."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.admin_service import AdminService

_EMAIL = "admin@example.com"
_PASSWORD = "supersecret"


async def test_create_then_authenticate(session: AsyncSession) -> None:
    service = AdminService(session)

    admin, created = await service.create_or_update(_EMAIL, _PASSWORD)

    assert created is True
    assert await service.authenticate(_EMAIL, _PASSWORD) is admin
    assert await service.authenticate(_EMAIL, "wrong") is None


async def test_create_or_update_resets_an_existing_password(session: AsyncSession) -> None:
    service = AdminService(session)
    await service.create_or_update(_EMAIL, _PASSWORD)

    admin, created = await service.create_or_update(_EMAIL, "a-new-password")

    assert created is False
    assert admin.email == _EMAIL
    assert await service.authenticate(_EMAIL, "a-new-password") is admin
    assert await service.authenticate(_EMAIL, _PASSWORD) is None


async def test_email_is_normalized_on_create_and_lookup(session: AsyncSession) -> None:
    service = AdminService(session)
    await service.create_or_update("Admin@Example.COM", _PASSWORD)

    assert await service.get_by_email("admin@example.com") is not None
    # A second create under different casing resets, never duplicates, the account.
    _, created = await service.create_or_update("ADMIN@example.com", "another-one")
    assert created is False


async def test_authenticate_unknown_email_returns_none(session: AsyncSession) -> None:
    assert await AdminService(session).authenticate("ghost@example.com", _PASSWORD) is None
