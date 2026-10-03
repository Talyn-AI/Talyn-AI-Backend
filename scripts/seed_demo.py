"""Seed the LIVE database with a demo dataset (learners + content to click).

Usage (from talyn-backend/):
    set DEMO_SEED_DATABASE_URL=postgresql://...   # the Supabase direct URL
    set DEMO_SEED_EMAILS=omolara@example.com,matthew@example.com,amina@example.com
    python scripts/seed_demo.py

Differs from scripts/seed.py on purpose:

  * seed.py is for local dev: known password (demo12345), localhost only.
  * This script is for a real database: every learner gets a RANDOM password
    that is printed ONCE to this terminal and never stored anywhere else.
    Hand each password to its owner over a channel you trust, and tell them
    to change it after first login (Profile > change password).

Idempotent: rerunning skips anything that already exists (by email / title /
template title). Safe to run twice; the second run prints nothing new.

Refuses to run without DEMO_SEED_CONFIRM_LIVE=1 when the target is not
localhost, so a stray invocation against production is a conscious act.
"""
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.onboarding import (  # noqa: E402
    INTEREST_KEYS,
    PACE_BY_KEY,
    WEEKLY_TARGET_BY_PACE,
)
from app.core.security import hash_password  # noqa: E402
from app.models import Course, Lesson, MissionTemplate, StudyPlan, User  # noqa: E402
from app.models.course import STATUS_PUBLISHED  # noqa: E402


NAMES = [
    ("Omolara Campbell", "omolara"),
    ("Matthew Ofori", "matthew"),
    ("Amina Lateef", "amina"),
]

LEARNER_DEFAULTS = {
    "difficulty_level": "beginner",
    "learning_pace": "steady",
    "interests": ["web-development", "design"],
}

CREATOR_EMAIL = "studio@talyn.dev"

COURSE = {
    "title": "UI/UX Design Fundamentals",
    "description": (
        "A hands-on introduction to user interface and experience design: "
        "typography, color, layout, and usable components."
    ),
    "difficulty_level": "beginner",
    "lessons": [
        {
            "order": 1,
            "title": "Design Basics: Typography",
            "topic": "Typography",
            "lesson_type": "lesson",
            "estimated_minutes": 12,
            "content": (
                "Learn type hierarchy, font pairing, and readable line "
                "lengths for interfaces."
            ),
        },
        {
            "order": 2,
            "title": "Color Theory for Interfaces",
            "topic": "Color Theory",
            "lesson_type": "lesson",
            "estimated_minutes": 15,
            "content": (
                "How to pick accessible palettes, contrast ratios, and "
                "semantic color."
            ),
        },
        {
            "order": 3,
            "title": "Layout & Spacing Systems",
            "topic": "Layout Systems",
            "lesson_type": "lesson",
            "estimated_minutes": 18,
            "content": "Grids, spacing scales, and alignment for consistent screens.",
        },
    ],
}

MISSION = {
    "title": "Build your first button",
    "description": "Design and code a real button component, then ship it.",
    "purpose": "Ship something real today",
    "reward_xp": 120,
    "badge": "Button Builder",
    "steps": [
        {"title": "Sketch the button", "description": "Pen and paper first.", "order": 1},
        {"title": "Code it in HTML/CSS", "description": "Make it clickable.", "order": 2},
    ],
}


def _database_url() -> str:
    url = os.environ.get("DEMO_SEED_DATABASE_URL", "")
    if not url:
        raise SystemExit(
            "Set DEMO_SEED_DATABASE_URL to the target database first. "
            "Refusing to guess which database you meant."
        )
    if "localhost" not in url and "127.0.0.1" not in url:
        if os.environ.get("DEMO_SEED_CONFIRM_LIVE") != "1":
            raise SystemExit(
                "Target is not localhost. Set DEMO_SEED_CONFIRM_LIVE=1 to "
                "confirm you mean the live database."
            )
    # The app normalizes postgres:// and bare postgresql:// itself; do the
    # same here so a pasted dashboard value just works.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def _learner_rows(emails: list[str]) -> list[dict]:
    if len(emails) != 3:
        raise SystemExit(
            f"Need exactly 3 emails in DEMO_SEED_EMAILS, got {len(emails)}."
        )
    rows = []
    for (name, _slug), email in zip(NAMES, emails):
        email = email.strip().lower()
        if "@" not in email:
            raise SystemExit(f"Not an email address: {email!r}")
        rows.append({"name": name, "email": email})
    if len({r["email"] for r in rows}) != 3:
        raise SystemExit("Emails must be distinct.")
    for interest in LEARNER_DEFAULTS["interests"]:
        if interest not in INTEREST_KEYS:
            raise SystemExit(f"Unknown interest key: {interest!r}")
    if LEARNER_DEFAULTS["learning_pace"] not in PACE_BY_KEY:
        raise SystemExit("Unknown pace key.")
    return rows


def main() -> None:
    from datetime import datetime, timezone

    url = _database_url()
    emails = [
        e for e in os.environ.get("DEMO_SEED_EMAILS", "").split(",") if e.strip()
    ]
    learners = _learner_rows(emails)
    now = datetime.now(timezone.utc)

    engine = create_engine(url)
    pace = PACE_BY_KEY[LEARNER_DEFAULTS["learning_pace"]]

    with Session(engine) as db:
        # Learners: verified + onboarded so they can act immediately, with a
        # study plan seeded from the pace exactly as /v1/onboarding/complete
        # would do it.
        fresh_credentials: list[tuple[str, str]] = []
        for row in learners:
            user = db.scalar(select(User).where(User.email == row["email"]))
            if user is not None:
                print(f"exists, skipped: {row['email']}")
                continue
            password = secrets.token_urlsafe(16)
            user = User(
                email=row["email"],
                password_hash=hash_password(password),
                learner_name=row["name"],
                difficulty_level=LEARNER_DEFAULTS["difficulty_level"],
                interests=list(LEARNER_DEFAULTS["interests"]),
                learning_pace=pace.key,
                email_verified_at=now,
                onboarding_completed_at=now,
            )
            db.add(user)
            db.flush()
            db.add(StudyPlan(
                user_id=user.id,
                daily_goal_minutes=pace.daily_goal_minutes,
                weekly_target_lessons=WEEKLY_TARGET_BY_PACE.get(pace.key, 5),
            ))
            fresh_credentials.append((row["email"], password))
            print(f"created learner: {row['name']} <{row['email']}>")

        # Demo creator behind the content.
        creator = db.scalar(select(User).where(User.email == CREATOR_EMAIL))
        if creator is None:
            creator = User(
                email=CREATOR_EMAIL,
                password_hash=hash_password(secrets.token_urlsafe(16)),
                learner_name="Talyn Studio",
                is_creator=True,
                interests=["design"],
                learning_pace="steady",
                email_verified_at=now,
                onboarding_completed_at=now,
            )
            db.add(creator)
            db.flush()
            print(f"created creator: {CREATOR_EMAIL} (random password, login via reset)")
        else:
            print(f"creator exists, skipped: {CREATOR_EMAIL}")

        # One published course so there is something to enrol in.
        course = db.scalar(select(Course).where(Course.title == COURSE["title"]))
        if course is None:
            course = Course(
                title=COURSE["title"],
                description=COURSE["description"],
                difficulty_level=COURSE["difficulty_level"],
                status=STATUS_PUBLISHED,
                creator_user_id=creator.id,
            )
            for lesson in COURSE["lessons"]:
                course.lessons.append(Lesson(**lesson))
            db.add(course)
            print(f"created course: {COURSE['title']} ({len(COURSE['lessons'])} lessons)")
        else:
            print(f"course exists, skipped: {COURSE['title']}")

        # One published mission so the catalogue is not empty.
        template = db.scalar(
            select(MissionTemplate).where(MissionTemplate.title == MISSION["title"])
        )
        if template is None:
            from app.models import MissionTemplateStep

            template = MissionTemplate(
                creator_user_id=creator.id,
                title=MISSION["title"],
                description=MISSION["description"],
                purpose=MISSION["purpose"],
                reward_xp=MISSION["reward_xp"],
                badge=MISSION["badge"],
                published=True,
            )
            for step in MISSION["steps"]:
                template.steps.append(MissionTemplateStep(**step))
            db.add(template)
            print(f"created mission: {MISSION['title']} (published)")
        else:
            print(f"mission exists, skipped: {MISSION['title']}")

        db.commit()

    if fresh_credentials:
        print()
        print("Temporary passwords — shown ONCE, never stored. Send each to its")
        print("owner privately and tell them to change it after first login.")
        for email, password in fresh_credentials:
            print(f"  {email}  /  {password}")
    else:
        print()
        print("Nothing new created; no passwords to distribute.")


if __name__ == "__main__":
    main()