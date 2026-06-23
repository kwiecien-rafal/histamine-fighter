"""Enable or disable an admin account from the command line.

The kill switch for an account that must lose access now: the auth gate re-reads
is_active on every request, so a deactivated account is locked out on its next call
without waiting for its token to expire. Reactivation restores access. Accounts are
created with create_admin; this only flips the active flag.

Run it (database up, migrations applied):

    uv run --directory backend python -m app.scripts.manage_admin --email you@example.com --deactivate
"""

import argparse
import asyncio
import sys

import structlog

from app.core.logging import configure_logging
from app.db.engine import SessionLocal
from app.services.user_service import UserService

log = structlog.get_logger()


def _validate_email(email: str) -> None:
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        sys.exit("Provide a valid email address.")


async def _run(email: str, active: bool) -> None:
    async with SessionLocal() as session:
        user = await UserService(session).set_active(email, active=active)
        if user is None:
            sys.exit(f"No account found for {email}.")
        await session.commit()
    log.info("manage_admin.done", email=user.email, active=active)


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description="Enable or disable an admin account.")
    parser.add_argument("--email", required=True, help="Email of the account to enable or disable.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--activate", dest="active", action="store_true", help="Re-enable the account."
    )
    group.add_argument(
        "--deactivate", dest="active", action="store_false", help="Disable the account."
    )
    args = parser.parse_args()
    email = str(args.email).strip()
    _validate_email(email)
    asyncio.run(_run(email, args.active))


if __name__ == "__main__":
    main()
