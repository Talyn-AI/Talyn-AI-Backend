"""Promote a learner to admin (course management) by email.

Usage (from talyn-backend/):
    python scripts/make_admin.py admin@example.com
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import User  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/make_admin.py <email>")
        sys.exit(1)

    email = sys.argv[1]
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            print(f"No account found for {email}")
            sys.exit(1)
        user.is_admin = True
        db.commit()
    print(f"{email} is now an admin")


if __name__ == "__main__":
    main()
