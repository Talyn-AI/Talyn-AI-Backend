"""Seed the dev database with a sample course and demo user.

Usage (from talyn-backend/):
    python scripts/seed.py

Idempotent: skips entities that already exist (EMEA by email/course title).
Defaults to the dev database (user talyn / talyn_dev_password @ localhost:5432 / talyn).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import Course, Lesson, User  # noqa: E402
from app.core.security import hash_password  # noqa: E402


DEMO_USER = {
    "email": "demo@talyn.dev",
    "password": "demo12345",
    "learner_name": "Demo Learner",
    "difficulty_level": "beginner",
    "goals": "Get comfortable building real web projects",
    "interests": ["web development", "design"],
}

COURSE = {
    "title": "UI/UX Design Fundamentals",
    "description": "A hands-on introduction to user interface and experience design: typography, color, layout, and usable components.",
    "difficulty_level": "beginner",
    "lessons": [
        {
            "order": 1,
            "title": "Design Basics: Typography",
            "topic": "Typography",
            "lesson_type": "lesson",
            "estimated_minutes": 12,
            "content": "Learn type hierarchy, font pairing, and readable line lengths for interfaces.",
        },
        {
            "order": 2,
            "title": "Color Theory for Interfaces",
            "topic": "Color Theory",
            "lesson_type": "lesson",
            "estimated_minutes": 15,
            "content": "How to pick accessible palettes, contrast ratios, and semantic color.",
        },
        {
            "order": 3,
            "title": "Layout & Spacing Systems",
            "topic": "Layout Systems",
            "lesson_type": "lesson",
            "estimated_minutes": 18,
            "content": "Grids, spacing scales, and alignment for consistent screens.",
        },
        {
            "order": 4,
            "title": "Mini-Quiz: Design Principles",
            "topic": "Design Principles",
            "lesson_type": "quiz",
            "estimated_minutes": 8,
            "content": "A short quiz on the principles covered so far.",
        },
        {
            "order": 5,
            "title": "Designing Usable Components",
            "topic": "UI Components",
            "lesson_type": "lesson",
            "estimated_minutes": 20,
            "content": "Buttons, inputs, cards, and navigation done right.",
        },
        {
            "order": 6,
            "title": "Handoff to Builders",
            "topic": "Design Handoff",
            "lesson_type": "lesson",
            "estimated_minutes": 10,
            "content": "Prototyping basics and handing designs to developers.",
        },
    ],
}


def main() -> None:
    with SessionLocal() as db:
        # Demo user
        user = db.scalar(select(User).where(User.email == DEMO_USER["email"]))
        if user is None:
            user = User(
                email=DEMO_USER["email"],
                password_hash=hash_password(DEMO_USER["password"]),
                learner_name=DEMO_USER["learner_name"],
                difficulty_level=DEMO_USER["difficulty_level"],
                goals=DEMO_USER["goals"],
                interests=DEMO_USER["interests"],
            )
            db.add(user)
            print(f"Created demo user: {DEMO_USER['email']}")
        else:
            print(f"Demo user already exists: {DEMO_USER['email']}")

        # Sample course
        course = db.scalar(select(Course).where(Course.title == COURSE["title"]))
        if course is None:
            course = Course(
                title=COURSE["title"],
                description=COURSE["description"],
                difficulty_level=COURSE["difficulty_level"],
            )
            for lesson in COURSE["lessons"]:
                course.lessons.append(Lesson(**lesson))
            db.add(course)
            print(f"Created course: {COURSE['title']} ({len(COURSE['lessons'])} lessons)")
        else:
            print(f"Course already exists: {COURSE['title']}")

        db.commit()

    print("Done. Login with demo@talyn.dev / demo12345 at /docs")


if __name__ == "__main__":
    main()