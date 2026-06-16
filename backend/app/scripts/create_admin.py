"""Create or reset an admin account from the command line.

The only way an admin account comes into existence (CLAUDE section 10): there is
no self-registration endpoint. Running it for an email that already exists resets
that account's password, so it doubles as a password-reset tool.

The password is read interactively (never echoed, never in the shell history). For
scripted setup, set ADMIN_PASSWORD in the environment instead, which keeps it off
the process's argument list.

Run it (database up, migrations applied):

    uv run --directory backend python -m app.scripts.create_admin --email you@example.com
"""

import argparse
import asyncio
import getpass
import os
import sys

import structlog

from app.core.logging import configure_logging
from app.core.security import MAX_PASSWORD_BYTES
from app.db.engine import SessionLocal
from app.services.admin_service import AdminService

log = structlog.get_logger()

_MIN_PASSWORD_CHARS = 8


def _read_password() -> str:
    """Read the password from ADMIN_PASSWORD, or prompt for it twice."""
    from_env = os.environ.get("ADMIN_PASSWORD")
    if from_env is not None:
        return from_env
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Confirm password: "):
        sys.exit("Passwords did not match.")
    return first


def _validate_email(email: str) -> None:
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        sys.exit("Provide a valid email address.")


def _validate_password(password: str) -> None:
    if len(password) < _MIN_PASSWORD_CHARS:
        sys.exit(f"Password must be at least {_MIN_PASSWORD_CHARS} characters.")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        sys.exit(f"Password must be at most {MAX_PASSWORD_BYTES} bytes.")


async def _run(email: str, password: str) -> None:
    async with SessionLocal() as session:
        admin, created = await AdminService(session).create_or_update(email, password)
        await session.commit()
    log.info("create_admin.done", email=admin.email, created=created)


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description="Create or reset an admin account.")
    parser.add_argument(
        "--email", required=True, help="Admin email (the login and audit identity)."
    )
    args = parser.parse_args()
    email = str(args.email).strip()
    _validate_email(email)
    password = _read_password()
    _validate_password(password)
    asyncio.run(_run(email, password))


if __name__ == "__main__":
    main()
