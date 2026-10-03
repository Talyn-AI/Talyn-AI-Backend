"""Set a displayable password for the seeded demo creator.

scripts/seed_demo.py generated a random password for studio@talyn.dev and
deliberately did not print it, on the reasoning that a creator account should
not ship with a known credential. That reasoning does not survive contact with
the actual demo: the creator dashboard is where enrolments and analytics are
shown, and an unreachable creator account means nobody can display them.

A password reset would be the normal route, but email delivery is blocked
until the sending domain is verified, so the reset link cannot arrive.

Prints the password once, exactly as seed_demo.py does. The account was
created by the seed script itself moments earlier and has no other owner, so
this locks nobody out.

Usage (from talyn-backend/):
    set DEMO_SEED_DATABASE_URL=postgresql://...
    set DEMO_SEED_CONFIRM_LIVE=1
    python scripts/set_demo_creator_password.py
"""
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.models import User  # noqa: E402

CREATOR_EMAIL = "studio@talyn.dev"


def _database_url() -> str:
    url = os.environ.get("DEMO_SEED_DATABASE_URL", "")
    if not url:
        raise SystemExit("Set DEMO_SEED_DATABASE_URL to the target database first.")
    if "localhost" not in url and "127.0.0.1" not in url:
        if os.environ.get("DEMO_SEED_CONFIRM_LIVE") != "1":
            raise SystemExit(
                "Target is not localhost. Set DEMO_SEED_CONFIRM_LIVE=1 to "
                "confirm you mean the live database."
            )
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def main() -> None:
    password = secrets.token_urlsafe(16)
    engine = create_engine(_database_url())
    with Session(engine) as db:
        user = db.scalar(select(User).where(User.email == CREATOR_EMAIL))
        if user is None:
            raise SystemExit(
                f"{CREATOR_EMAIL} not found; run scripts/seed_demo.py first."
            )
        user.password_hash = hash_password(password)
        db.commit()

    print("Demo creator password — shown ONCE, never stored.")
    print(f"  {CREATOR_EMAIL}  /  {password}")


if __name__ == "__main__":
    main()
