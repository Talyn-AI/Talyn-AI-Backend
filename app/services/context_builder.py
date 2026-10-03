"""Progress reading: assemble LearnerContext from real DB state."""
import json
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Badge,
    ConversationMessage,
    Course,
    Enrollment,
    Lesson,
    LessonProgress,
    Mission,
    QuizResult,
    StudyPlan,
    User,
    XpEvent,
)
from app.services.xp import level_info, streak_days, xp_this_week, xp_total
from app.models.course import STATUS_PUBLISHED

CONTEXT_HISTORY_LIMIT = 20
DEFAULT_HOURS_PER_WEEK = 5.0
DEFAULT_DAILY_MINUTES = 30


def study_plan_payload(db: Session, user: User) -> dict | None:
    """The learner's study plan with focus_topics parsed, or None if unset."""
    plan = db.scalar(select(StudyPlan).where(StudyPlan.user_id == user.id))
    if plan is None:
        return None
    try:
        topics = json.loads(plan.focus_topics or "[]")
        if not isinstance(topics, list):
            topics = []
    except (ValueError, TypeError):
        topics = []
    return {
        "daily_goal_minutes": plan.daily_goal_minutes,
        "weekly_target_lessons": plan.weekly_target_lessons,
        "focus_topics": [str(t) for t in topics],
        "deadline": plan.deadline,
    }


def quiz_performance(db: Session, user: User) -> list[dict]:
    """Per-topic aggregate: best score, total attempts, latest attempt date."""
    rows = db.execute(
        select(
            QuizResult.topic,
            func.max(QuizResult.score_percent).label("best"),
            func.sum(QuizResult.attempts).label("attempts"),
            func.max(QuizResult.taken_at).label("last"),
        )
        .where(QuizResult.user_id == user.id)
        .group_by(QuizResult.topic)
    ).all()
    return [
        {
            "topic": r.topic,
            "score_percent": round(float(r.best), 1),
            "attempts": int(r.attempts),
            "last_attempt_date": str(r.last.date()) if r.last else "",
        }
        for r in rows
    ]


def lessons_total(db: Session, user: User) -> int:
    """Lessons in the learner's enrolled courses; fall back to all published."""
    course_ids = db.scalars(
        select(Enrollment.course_id).where(Enrollment.user_id == user.id)
    ).all()
    if course_ids:
        return int(
            db.scalar(
                select(func.count(Lesson.id)).where(
                    Lesson.course_id.in_(course_ids), Lesson.is_published.is_(True)
                )
            )
            or 0
        )
    return int(
        db.scalar(
            select(func.count(Lesson.id)).where(Lesson.is_published.is_(True))
        )
        or 0
    )


def lessons_completed(db: Session, user: User) -> int:
    return int(
        db.scalar(
            select(func.count(LessonProgress.id)).where(
                LessonProgress.user_id == user.id,
                LessonProgress.completed_at.is_not(None),
            )
        )
        or 0
    )


def build_learner_context(db: Session, user: User) -> dict:
    """Assemble the LearnerContext dictionary the AI coach expects."""
    completed = lessons_completed(db, user)
    total = lessons_total(db, user)
    total_xp = xp_total(db, user)

    recent_xp = db.scalars(
        select(XpEvent)
        .where(XpEvent.user_id == user.id)
        .order_by(XpEvent.earned_date.desc())
        .limit(20)
    ).all()

    badges = db.scalars(
        select(Badge).where(Badge.user_id == user.id).order_by(Badge.earned_date.desc())
    ).all()

    active_mission = db.scalar(
        select(Mission)
        .where(Mission.user_id == user.id, Mission.status == "in_progress")
        .order_by(Mission.id.desc())
    )
    completed_mission_count = int(
        db.scalar(
            select(func.count(Mission.id)).where(
                Mission.user_id == user.id, Mission.status == "completed"
            )
        )
        or 0
    )
    completed_mission_ids = [
        str(mid)
        for mid in db.scalars(
            select(Mission.id)
            .where(Mission.user_id == user.id, Mission.status == "completed")
            .order_by(Mission.id)
        ).all()
    ]

    missions_payload = {
        "active_mission": None,
        "completed_mission_ids": completed_mission_ids,
        "completed_mission_count": completed_mission_count,
    }
    if active_mission is not None:
        missions_payload["active_mission"] = {
            "mission_id": str(active_mission.id),
            "title": active_mission.title,
            "description": active_mission.description,
            "purpose": active_mission.purpose,
            "reward_xp": active_mission.reward_xp,
            "badge": active_mission.badge,
            "status": active_mission.status,
            "steps": [
                {"step_id": str(s.id), "title": s.title, "description": s.description,
                 "order": s.order, "completed": s.completed}
                for s in active_mission.steps
            ],
        }

    return {
        "learner_id": str(user.id),
        "learner_name": user.learner_name,
        "current_course": user.current_course,
        "current_lesson": user.current_lesson,
        "current_topic": user.current_topic,
        "interests": user.interests or [],
        "difficulty_level": user.difficulty_level,
        "goals": user.goals,
        "xp_total": total_xp,
        "xp_this_week": xp_this_week(db, user),
        "streak_days": streak_days(db, user),
        "lessons_completed": completed,
        "lessons_total": total,
        "completion_percent": round(completed / total * 100, 1) if total else 0.0,
        "quiz_performance": quiz_performance(db, user),
        "level": level_info(total_xp),
        "badges": [
            {"badge_id": b.badge_id, "name": b.name, "description": b.description,
             "icon": b.icon, "earned_date": str(b.earned_date.date())}
            for b in badges
        ],
        "xp_breakdown": [
            {"activity": x.activity, "amount": x.amount, "note": x.note,
             "earned_date": x.earned_date.isoformat()}
            for x in recent_xp
        ],
        "missions": missions_payload,
        "study_plan": study_plan_payload(db, user),
        "conversation_history": conversation_history(db, user),
    }


def conversation_history(db: Session, user: User) -> list[dict]:
    """The learner's most recent chat turns, chronological, for the AI coach."""
    rows = db.scalars(
        select(ConversationMessage)
        .where(ConversationMessage.user_id == user.id)
        .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
        .limit(CONTEXT_HISTORY_LIMIT)
    ).all()
    return [{"role": m.role, "content": m.content} for m in reversed(rows)]


def personalization_profile(user: User) -> dict:
    """Minimal profile for content personalization, straight off the user row."""
    return {
        "learner_id": str(user.id),
        "learner_name": user.learner_name,
        "interests": user.interests or [],
        "difficulty_level": user.difficulty_level,
        "current_course": user.current_course,
        "current_topic": user.current_topic,
    }


def _hours_per_week(db: Session, user: User) -> float:
    plan = db.scalar(select(StudyPlan).where(StudyPlan.user_id == user.id))
    if plan is None:
        return DEFAULT_HOURS_PER_WEEK
    return round(plan.daily_goal_minutes * 7 / 60, 1)


def path_profile(db: Session, user: User) -> dict:
    """Profile for learning-path generation: completed + available courses."""
    completed_ids = [
        str(e.course_id)
        for e in db.scalars(
            select(Enrollment).where(
                Enrollment.user_id == user.id, Enrollment.completed.is_(True)
            )
        ).all()
    ]
    courses = db.scalars(
        select(Course)
        .where(Course.status == STATUS_PUBLISHED)
        .order_by(Course.id)
    ).all()
    available = []
    for course in courses:
        lessons = db.scalars(
            select(Lesson)
            .where(Lesson.course_id == course.id, Lesson.is_published.is_(True))
            .order_by(Lesson.order)
        ).all()
        minutes = sum(lesson.estimated_minutes for lesson in lessons)
        topics = list(dict.fromkeys(lesson.topic for lesson in lessons))
        available.append(
            {
                "course_id": str(course.id),
                "title": course.title,
                "description": course.description,
                "skill_level": course.difficulty_level,
                "estimated_hours": round(minutes / 60, 1),
                "topics": topics,
            }
        )
    return {
        "learner_id": str(user.id),
        "learner_name": user.learner_name,
        "skill_level": user.difficulty_level,
        "interests": user.interests or [],
        "hours_per_week": _hours_per_week(db, user),
        "goals": user.goals,
        "completed_course_ids": completed_ids,
        "available_courses": available,
    }


def revision_profile(db: Session, user: User) -> dict:
    """Revision profile: one record per encountered topic."""
    # UTC, not date.today(). Timestamps are stored as timestamptz and read back
    # in UTC, so comparing them against the server's local date makes anything
    # studied "today" read as 1 day old for part of the day. That quietly
    # changes revision priorities for every learner in that window.
    today = datetime.now(timezone.utc).date()
    records: dict[str, dict] = {}

    completed = db.execute(
        select(LessonProgress, Lesson)
        .join(Lesson, Lesson.id == LessonProgress.lesson_id)
        .where(
            LessonProgress.user_id == user.id,
            LessonProgress.completed_at.is_not(None),
        )
    ).all()
    for progress, lesson in completed:
        studied = progress.completed_at.date()
        records[lesson.topic] = {
            "topic": lesson.topic,
            "lesson": lesson.title,
            "last_studied_date": studied.isoformat(),
            "days_since_studied": (today - studied).days,
            "times_reviewed": progress.attempts,
            "quiz_score_percent": None,
            "lesson_completed": True,
        }

    quizzes = db.execute(
        select(
            QuizResult.topic,
            func.max(QuizResult.score_percent).label("best"),
            func.max(QuizResult.taken_at).label("last"),
        )
        .where(QuizResult.user_id == user.id)
        .group_by(QuizResult.topic)
    ).all()
    for topic, best, last in quizzes:
        taken = last.date() if last else today
        entry = records.get(topic)
        if entry is None:
            lesson_title = db.scalar(
                select(Lesson.title).where(Lesson.topic == topic).limit(1)
            )
            records[topic] = {
                "topic": topic,
                "lesson": lesson_title or "",
                "last_studied_date": taken.isoformat(),
                "days_since_studied": (today - taken).days,
                "times_reviewed": 0,
                "quiz_score_percent": round(float(best), 1),
                "lesson_completed": False,
            }
        else:
            entry["quiz_score_percent"] = round(float(best), 1)
            if taken > date.fromisoformat(entry["last_studied_date"]):
                entry["last_studied_date"] = taken.isoformat()
                entry["days_since_studied"] = (today - taken).days

    plan = db.scalar(select(StudyPlan).where(StudyPlan.user_id == user.id))
    return {
        "learner_id": str(user.id),
        "learner_name": user.learner_name,
        "difficulty_level": user.difficulty_level,
        "daily_study_minutes": plan.daily_goal_minutes if plan else DEFAULT_DAILY_MINUTES,
        "topic_records": sorted(records.values(), key=lambda r: r["topic"]),
        "schedule_start_date": today.isoformat(),
    }