"""Give the demo learners real activity: enrolments, progress, XP, analytics.

Usage (from talyn-backend/):
    set DEMO_SEED_DATABASE_URL=postgresql://...
    set DEMO_SEED_CONFIRM_LIVE=1
    python scripts/seed_demo_activity.py

Companion to scripts/seed_demo.py, which creates the accounts and content.
This one makes them look used, because an empty dashboard proves nothing to
an investor: enrolments spread over a week so the creator's daily chart has
more than one bar, three learners at visibly different stages, quizzes with
scores that differ, and one half-finished mission.

Everything is written through the same columns the real endpoints write, with
timestamps set explicitly so activity lands on past days rather than all
today. The XP amounts come from the canonical XP_AMOUNTS table and the quiz
formula mirrors award_quiz_xp, so totals match what the app would have
awarded had the work been done through the UI.

Idempotent: it skips all activity once any XP exists for these learners, so a
second run cannot double-award. The rename and pace fixup run every time.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.orm import Session, selectinload  # noqa: E402

from app.core.onboarding import (  # noqa: E402
    PACE_BY_KEY,
    WEEKLY_TARGET_BY_PACE,
)
from app.models import (  # noqa: E402
    AnalyticsEvent,
    Course,
    Enrollment,
    Lesson,
    LessonProgress,
    Mission,
    MissionStep,
    MissionTemplate,
    QuizResult,
    StudyPlan,
    User,
    XpEvent,
)
from app.models.analytics import (  # noqa: E402
    COURSE_COMPLETED,
    COURSE_ENROLLED,
    COURSE_PUBLISHED,
    LESSON_COMPLETED,
    LESSON_STARTED,
)
from app.models.course import STATUS_PUBLISHED  # noqa: E402
from app.services.analytics import track  # noqa: E402
from app.services.xp import XP_AMOUNTS  # noqa: E402

AMINA_EMAIL = "alabiadeolamartins@gmail.com"
AMINA_NEW_NAME = "Amina Lateef"

# Each learner's story, told as offsets from today. Different stages on
# purpose: a demo where everyone is identical cannot show progression.
#
#   Omolara  - finished the course, sitting on level 2, mission underway
#   Matthew  - halfway through, last lesson open
#   Amina    - just started yesterday
PLAN = {
    "talyned.ai@gmail.com": {
        "enroll": 6,
        "complete": [(1, 5), (2, 4), (3, 3)],
        "quizzes": [("Typography", 85.0, 3), ("Color Theory", 92.0, 2)],
        "start": [],
        "course_completed": 3,
        "mission": {"adopt": 2, "complete_steps": [1]},
    },
    "qweensmart964@gmail.com": {
        "enroll": 4,
        "complete": [(1, 3), (2, 1)],
        "quizzes": [("Layout Systems", 76.0, 1)],
        "start": [3],
        "course_completed": None,
        "mission": None,
    },
    AMINA_EMAIL: {
        "enroll": 2,
        "complete": [(1, 0)],
        "quizzes": [],
        "start": [2],
        "course_completed": None,
        "mission": None,
    },
}


def at(days_ago: int, hour: int = 10) -> datetime:
    """A UTC timestamp on a past day, so analytics buckets spread out.

    Day 0 is an hour ago rather than a chosen hour: picking 10:00 for today
    would put an event in the future whenever the script runs before then.
    """
    if days_ago == 0:
        return datetime.now(timezone.utc) - timedelta(hours=1)
    base = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return base.replace(hour=hour, minute=0, second=0, microsecond=0)


def quiz_xp(score_percent: float) -> int:
    """Mirror of award_quiz_xp's arithmetic: base + 1 per 10 points."""
    return XP_AMOUNTS["quiz"] + int(score_percent // 10)


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
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def _lessons_by_order(db: Session, course: Course) -> dict[int, Lesson]:
    rows = db.scalars(
        select(Lesson).where(Lesson.course_id == course.id)
    ).all()
    return {lesson.order: lesson for lesson in rows}


def _fixup_amina(db: Session) -> User:
    """Rename the pre-existing account and seed its pace and study plan.

    The row already existed under a different name, so the account-creation
    pass in seed_demo.py skipped it. It is verified and onboarded (the
    migration backfilled that), but its pace was left null.

    The study plan is aligned to the pace unconditionally, rather than only
    when left on the default. This account carried a goal of 90 minutes that
    matches no pace option, with its weekly target still at the default —
    stray values from earlier testing, not a choice anyone made. Pace seeding
    the plan is the same rule /v1/onboarding/complete applies, so a demo
    account ends up exactly as a real one that picked "steady" would.
    """
    user = db.scalar(select(User).where(User.email == AMINA_EMAIL))
    if user is None:
        raise SystemExit(f"{AMINA_EMAIL} not found; run scripts/seed_demo.py first")

    changed = []
    if user.learner_name != AMINA_NEW_NAME:
        user.learner_name = AMINA_NEW_NAME
        changed.append("name")
    if user.learning_pace is None:
        user.learning_pace = "steady"
        changed.append("pace")

    pace = PACE_BY_KEY[user.learning_pace]
    plan = db.scalar(select(StudyPlan).where(StudyPlan.user_id == user.id))
    if plan is None:
        plan = StudyPlan(
            user_id=user.id,
            daily_goal_minutes=pace.daily_goal_minutes,
            weekly_target_lessons=WEEKLY_TARGET_BY_PACE.get(pace.key, 5),
        )
        db.add(plan)
        changed.append("study plan")
    elif (
        plan.daily_goal_minutes,
        plan.weekly_target_lessons,
    ) != (
        pace.daily_goal_minutes,
        WEEKLY_TARGET_BY_PACE.get(pace.key, plan.weekly_target_lessons),
    ):
        plan.daily_goal_minutes = pace.daily_goal_minutes
        plan.weekly_target_lessons = WEEKLY_TARGET_BY_PACE.get(pace.key, 5)
        changed.append(
            f"study plan goal -> {plan.daily_goal_minutes} min / "
            f"{plan.weekly_target_lessons} lessons"
        )

    print(f"amina: {', '.join(changed) if changed else 'already correct'}")
    return user


def main() -> None:
    url = _database_url()
    engine = create_engine(url)

    with Session(engine) as db:
        course = db.scalar(
            select(Course).where(Course.title == "UI/UX Design Fundamentals")
        )
        if course is None or course.status != STATUS_PUBLISHED:
            raise SystemExit(
                "Published demo course not found; run scripts/seed_demo.py first."
            )
        lessons = _lessons_by_order(db, course)
        if not lessons:
            raise SystemExit("Course has no lessons.")

        users = {
            email: db.scalar(select(User).where(User.email == email))
            for email in PLAN
        }
        missing = [e for e, u in users.items() if u is None]
        if missing:
            raise SystemExit(
                f"Missing learners: {', '.join(missing)}. "
                "Run scripts/seed_demo.py first."
            )

        _fixup_amina(db)

        already = db.scalar(
            select(func.count(XpEvent.id)).where(
                XpEvent.user_id.in_([u.id for u in users.values()])
            )
        )
        if already:
            db.commit()
            print(f"activity already seeded ({already} XP events); nothing to do")
            return

        template = db.scalar(
            select(MissionTemplate)
            .where(MissionTemplate.published.is_(True))
            .options(selectinload(MissionTemplate.steps))
        )

        for email, spec in PLAN.items():
            user = users[email]
            tag = user.learner_name

            enrolled = db.scalar(
                select(Enrollment).where(
                    Enrollment.user_id == user.id,
                    Enrollment.course_id == course.id,
                )
            )
            if enrolled is None:
                enrolled = Enrollment(
                    user_id=user.id,
                    course_id=course.id,
                    enrolled_at=at(spec["enroll"], 9),
                    source="direct",
                )
                db.add(enrolled)
            track(
                db, COURSE_ENROLLED, user,
                course_id=course.id,
                meta={"source": "direct"},
            )
            db.flush()
            # Analytics rows are bucketed by day; pin this to the enrol day so
            # the chart shows a week of activity rather than one lump.
            _pin_last_event(db, at(spec["enroll"], 9))

            for order, days_ago in spec["complete"]:
                lesson = lessons[order]
                db.add(LessonProgress(
                    user_id=user.id,
                    lesson_id=lesson.id,
                    completed_at=at(days_ago, 11),
                    attempts=1,
                ))
                db.add(XpEvent(
                    user_id=user.id,
                    activity="lesson",
                    amount=XP_AMOUNTS["lesson"],
                    note=f"Completed lesson: {lesson.title}",
                    earned_date=at(days_ago, 11),
                ))
                track(
                    db, LESSON_COMPLETED, user,
                    course_id=course.id, lesson_id=lesson.id,
                )
                _pin_last_event(db, at(days_ago, 11))

            for order in spec["start"]:
                lesson = lessons[order]
                db.add(LessonProgress(
                    user_id=user.id,
                    lesson_id=lesson.id,
                    completed_at=None,
                    attempts=1,
                ))
                track(
                    db, LESSON_STARTED, user,
                    course_id=course.id, lesson_id=lesson.id,
                )
                _pin_last_event(db, at(0, 9))

            for topic, score, days_ago in spec["quizzes"]:
                db.add(QuizResult(
                    user_id=user.id,
                    course_id=course.id,
                    topic=topic,
                    score_percent=score,
                    attempts=1,
                    taken_at=at(days_ago, 12),
                ))
                db.add(XpEvent(
                    user_id=user.id,
                    activity="quiz",
                    amount=quiz_xp(score),
                    note=f"Quiz: {topic} ({score:.0f}%)",
                    earned_date=at(days_ago, 12),
                ))

            if spec["course_completed"] is not None:
                enrolled.completed = True
                track(db, COURSE_COMPLETED, user, course_id=course.id)
                _pin_last_event(db, at(spec["course_completed"], 12))

            mission_spec = spec.get("mission")
            if mission_spec and template is not None:
                mission = Mission(
                    user_id=user.id,
                    template_id=template.id,
                    title=template.title,
                    description=template.description,
                    purpose=template.purpose,
                    reward_xp=template.reward_xp,
                    badge=template.badge,
                    status="in_progress",
                )
                for step in template.steps:
                    done = step.order in mission_spec["complete_steps"]
                    mission.steps.append(MissionStep(
                        title=step.title,
                        description=step.description,
                        order=step.order,
                        completed=done,
                    ))
                db.add(mission)
                print(f"{tag}: adopted mission {template.title!r} "
                      f"({len(mission_spec['complete_steps'])} of "
                      f"{len(template.steps)} steps done)")

            print(f"{tag}: {len(spec['complete'])} lessons, "
                  f"{len(spec['quizzes'])} quizzes, "
                  f"{len(spec['start'])} in progress")

        # One creator-side event so the dashboard is not blank before anyone
        # enrols: the day the course went out.
        creator_id = course.creator_user_id
        if creator_id is not None:
            creator = db.get(User, creator_id)
            if creator is not None:
                track(db, COURSE_PUBLISHED, creator, course_id=course.id)
                _pin_last_event(db, at(6, 8))

        db.commit()

    print()
    print("Activity seeded. Expected XP totals:")
    for email, spec in PLAN.items():
        total = (
            len(spec["complete"]) * XP_AMOUNTS["lesson"]
            + sum(quiz_xp(s) for _t, s, _d in spec["quizzes"])
        )
        level = 1 + total // 100
        print(f"  {email}  {total} XP  (level {level})")


def _pin_last_event(db: Session, when: datetime) -> None:
    """Backdate the analytics row track() just added.

    track() stamps its own timestamp, but seeding activity onto past days is
    the whole point here — otherwise every event lands on today and the
    creator's daily chart shows a single bar. The newest row is by definition
    the one track() just appended, since the session flushes before this runs
    and nothing else writes events during seeding.
    """
    db.flush()
    row = db.scalar(
        select(AnalyticsEvent)
        .order_by(AnalyticsEvent.id.desc())
        .limit(1)
    )
    if row is not None:
        row.created_at = when


if __name__ == "__main__":
    main()
