"""Progress router: enroll, complete lessons, submit quizzes, XP and context."""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, require_onboarding
from app.database import get_db
from app.models import (
    Badge,
    ConversationMessage,
    Course,
    Enrollment,
    Lesson,
    LessonProgress,
    QuizResult,
    StudyPlan,
    User,
    XpEvent,
)
from app.schemas import (
    CONVERSATION_ROLES,
    MANUAL_XP_ACTIVITIES,
    BadgeRead,
    ConversationIn,
    ConversationRead,
    EnrollmentProgress,
    LearnerContextOut,
    LessonCompleteRequest,
    QuizSubmit,
    StudyPlanIn,
    StudyPlanRead,
    XpAwardIn,
    XpEntry,
    XpSummary,
)
from app.services.context_builder import (
    build_learner_context,
    path_profile,
    personalization_profile,
    revision_profile,
    study_plan_payload,
)
from app.services.xp import (
    XP_AMOUNTS,
    award_quiz_xp,
    award_xp,
    level_info,
    xp_this_week,
    xp_total,
)
from app.services.analytics import track
from app.models.analytics import (
    COURSE_COMPLETED,
    COURSE_ENROLLED,
    LESSON_COMPLETED,
    LESSON_STARTED,
)

router = APIRouter(prefix="/me", tags=["Progress"])


# ── Enrollment ────────────────────────────────────────────────────────────────

@router.post("/enroll/{course_id}", status_code=status.HTTP_201_CREATED)
def enroll(
    course_id: int,
    current_user: User = Depends(require_onboarding),
    db: Session = Depends(get_db),
) -> dict:
    """Enroll the authenticated learner in a course."""
    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")

    existing = db.scalar(
        select(Enrollment).where(
            Enrollment.user_id == current_user.id, Enrollment.course_id == course_id
        )
    )
    if existing is not None:
        return {"enrolled": True, "course_id": course_id, "message": "Already enrolled"}

    db.add(Enrollment(user_id=current_user.id, course_id=course_id,
                      source="direct"))
    track(db, COURSE_ENROLLED, current_user, course_id=course_id,
          meta={"source": "direct"})
    db.commit()
    return {"enrolled": True, "course_id": course_id, "message": "Enrolled"}


# ── Lesson completion ────────────────────────────────────────────────────────

@router.post("/lessons/{lesson_id}/complete")
def complete_lesson(
    lesson_id: int,
    payload: LessonCompleteRequest = LessonCompleteRequest(),
    current_user: User = Depends(require_onboarding),
    db: Session = Depends(get_db),
) -> dict:
    """Mark a lesson complete; awards lesson XP (once per lesson)."""
    lesson = db.get(Lesson, lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    _require_progress_access(db, current_user, lesson)

    progress = db.scalar(
        select(LessonProgress).where(
            LessonProgress.user_id == current_user.id,
            LessonProgress.lesson_id == lesson_id,
        )
    )
    already_done = False
    if progress is None:
        progress = LessonProgress(
            user_id=current_user.id, lesson_id=lesson_id, completed_at=datetime.now(timezone.utc), attempts=1
        )
        db.add(progress)
    elif progress.completed_at is not None:
        already_done = True
    else:
        progress.completed_at = datetime.now(timezone.utc)
        progress.attempts += 1

    if not already_done:
        award_xp(db, current_user, "lesson", note=f"Completed lesson: {lesson.title}")
        track(db, LESSON_COMPLETED, current_user, course_id=lesson.course_id,
              lesson_id=lesson.id)
    else:
        return {"completed": True, "lesson_id": lesson_id, "xp_awarded": 0, "already_completed": True}

    # Flush so the new progress row is visible to the course-progress count
    # below (sessions run with autoflush off).
    db.flush()
    _maybe_complete_enrollment(db, current_user, lesson.course_id)

    db.commit()
    return {
        "completed": True,
        "lesson_id": lesson_id,
        "xp_awarded": XP_AMOUNTS["lesson"],
        "already_completed": False,
    }


# ── Quiz submission ──────────────────────────────────────────────────────────

@router.post("/quiz-results", response_model=QuizSubmit)
def submit_quiz(
    payload: QuizSubmit,
    current_user: User = Depends(require_onboarding),
    db: Session = Depends(get_db),
) -> QuizSubmit:
    """Record a quiz attempt and award quiz XP."""
    result = QuizResult(
        user_id=current_user.id,
        course_id=payload.course_id,
        topic=payload.topic,
        score_percent=payload.score_percent,
        attempts=payload.attempts,
    )
    db.add(result)
    award_quiz_xp(db, current_user, payload.score_percent, note=f"Quiz: {payload.topic} ({payload.score_percent:.0f}%)")
    db.commit()
    return payload


# ── XP summary ───────────────────────────────────────────────────────────────

@router.get("/xp", response_model=XpSummary)
def xp_summary(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> XpSummary:
    """The learner's XP totals, level, and recent breakdown."""
    total = xp_total(db, current_user)
    events = db.scalars(
        select(XpEvent)
        .where(XpEvent.user_id == current_user.id)
        .order_by(XpEvent.earned_date.desc())
        .limit(20)
    ).all()
    li = level_info(total)
    return XpSummary(
        xp_total=total,
        xp_this_week=xp_this_week(db, current_user),
        level=li["level"],
        level_title=li["title"],
        xp_this_level=li["xp_this_level"],
        level_up_xp=li["level_up_xp"],
        next_level_title=li["next_level_title"],
        breakdown=[XpEntry(activity=e.activity, amount=e.amount, note=e.note, earned_date=e.earned_date) for e in events],
    )


# ── Full learner context (the AI coach contract) ─────────────────────────────

@router.get("/context", response_model=LearnerContextOut)
def learner_context(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LearnerContextOut:
    """Assemble the LearnerContext object the AI coach expects, from real DB data."""
    return LearnerContextOut(**build_learner_context(db, current_user))


# ── Study plan (one per learner) ─────────────────────────────────────────────

@router.put("/study-plan", response_model=StudyPlanRead)
def upsert_study_plan(
    payload: StudyPlanIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StudyPlanRead:
    """Create or replace the learner's study plan."""
    plan = db.scalar(select(StudyPlan).where(StudyPlan.user_id == current_user.id))
    if plan is None:
        plan = StudyPlan(user_id=current_user.id)
        db.add(plan)
    plan.daily_goal_minutes = payload.daily_goal_minutes
    plan.weekly_target_lessons = payload.weekly_target_lessons
    plan.focus_topics = json.dumps(payload.focus_topics)
    plan.deadline = payload.deadline
    db.commit()
    db.refresh(plan)
    return StudyPlanRead(
        id=plan.id,
        daily_goal_minutes=plan.daily_goal_minutes,
        weekly_target_lessons=plan.weekly_target_lessons,
        focus_topics=payload.focus_topics,
        deadline=plan.deadline,
    )


@router.get("/study-plan", response_model=StudyPlanRead)
def get_study_plan(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StudyPlanRead:
    """Get the learner's study plan, or 404 if they haven't set one."""
    plan = db.scalar(select(StudyPlan).where(StudyPlan.user_id == current_user.id))
    if plan is None:
        raise HTTPException(status_code=404, detail="No study plan set")
    payload = study_plan_payload(db, current_user)
    assert payload is not None
    return StudyPlanRead(id=plan.id, **payload)


# ── Enrollments with per-course progress ─────────────────────────────────────
def _course_progress(db: Session, user: User, course_id: int) -> tuple[int, int]:
    """Return (lessons_total, lessons_completed) for one course."""
    total = int(
        db.scalar(
            select(func.count(Lesson.id)).where(
                Lesson.course_id == course_id, Lesson.is_published.is_(True)
            )
        )
        or 0
    )
    done = int(
        db.scalar(
            select(func.count(LessonProgress.id))
            .join(Lesson, Lesson.id == LessonProgress.lesson_id)
            .where(
                LessonProgress.user_id == user.id,
                Lesson.course_id == course_id,
                LessonProgress.completed_at.is_not(None),
            )
        )
        or 0
    )
    return total, done


def _require_progress_access(db: Session, user: User, lesson: Lesson) -> None:
    """Progress recording needs an enrollment; managers bypass (preview).

    Applies on drafts too — previously any authenticated user could earn
    XP on unpublished lessons just by guessing lesson ids.
    """
    from app.core.deps import can_manage_course

    course = db.get(Course, lesson.course_id)
    if course is not None and can_manage_course(course, user):
        return
    enrolled = db.scalar(
        select(Enrollment).where(
            Enrollment.user_id == user.id,
            Enrollment.course_id == lesson.course_id,
        )
    )
    if enrolled is None:
        raise HTTPException(status_code=403, detail="Enroll in the course first")


def _maybe_complete_enrollment(db: Session, user: User, course_id: int) -> None:
    """Flip enrollment.completed once every published lesson is done."""
    total, done = _course_progress(db, user, course_id)
    if total and done >= total:
        enrollment = db.scalar(
            select(Enrollment).where(
                Enrollment.user_id == user.id, Enrollment.course_id == course_id
            )
        )
        if enrollment is not None and not enrollment.completed:
            enrollment.completed = True
            track(db, COURSE_COMPLETED, user, course_id=course_id)


@router.post("/lessons/{lesson_id}/start")
def start_lesson(
    lesson_id: int,
    current_user: User = Depends(require_onboarding),
    db: Session = Depends(get_db),
) -> dict:
    """Record that the learner started a lesson (idempotent, no XP)."""
    lesson = db.get(Lesson, lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    _require_progress_access(db, current_user, lesson)

    progress = db.scalar(
        select(LessonProgress).where(
            LessonProgress.user_id == current_user.id,
            LessonProgress.lesson_id == lesson_id,
        )
    )
    if progress is None:
        progress = LessonProgress(user_id=current_user.id, lesson_id=lesson_id)
        db.add(progress)
        track(db, LESSON_STARTED, current_user, course_id=lesson.course_id,
              lesson_id=lesson.id)
        db.commit()
        return {"started": True, "lesson_id": lesson_id, "first_time": True}
    db.commit()
    return {"started": True, "lesson_id": lesson_id, "first_time": False}


@router.get("/enrollments", response_model=list[EnrollmentProgress])
def list_enrollments(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[EnrollmentProgress]:
    """The learner's enrollments with per-course progress aggregation."""
    rows = db.scalars(
        select(Enrollment)
        .where(Enrollment.user_id == current_user.id)
        .order_by(Enrollment.enrolled_at)
        .limit(limit)
        .offset(offset)
    ).all()
    out = []
    for enrollment in rows:
        course = db.get(Course, enrollment.course_id)
        if course is None:
            continue
        total, done = _course_progress(db, current_user, enrollment.course_id)
        out.append(
            EnrollmentProgress(
                course_id=course.id,
                title=course.title,
                difficulty_level=course.difficulty_level,
                lessons_total=total,
                lessons_completed=done,
                completion_percent=round(done / total * 100, 1) if total else 0.0,
                completed=enrollment.completed,
                enrolled_at=enrollment.enrolled_at,
            )
        )
    return out


@router.delete("/study-plan")
def delete_study_plan(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Clear the learner's study plan."""
    plan = db.scalar(select(StudyPlan).where(StudyPlan.user_id == current_user.id))
    if plan is None:
        raise HTTPException(status_code=404, detail="No study plan set")
    db.delete(plan)
    db.commit()
    return {"message": "Study plan cleared"}


# ── Generic XP awards (activities without a dedicated endpoint) ───────────────

@router.post("/xp/award", response_model=XpEntry, status_code=status.HTTP_201_CREATED)
def award_manual_xp(
    payload: XpAwardIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> XpEvent:
    """Award XP for revision, challenge, live participation, or streaks.

    lesson/quiz/mission are rejected here — they have dedicated endpoints
    (lesson completion, quiz submit, mission complete) so each XP source
    keeps a single source of truth.
    """
    if payload.activity not in MANUAL_XP_ACTIVITIES:
        raise HTTPException(
            status_code=422,
            detail=f"Use the dedicated endpoint for '{payload.activity}' XP",
        )
    xp = award_xp(db, current_user, payload.activity, note=payload.note)
    db.commit()
    db.refresh(xp)
    return xp


# ── Badges ────────────────────────────────────────────────────────────────────

@router.get("/badges", response_model=list[BadgeRead])
def list_badges(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Badge]:
    """List the learner's earned badges, newest first."""
    return list(
        db.scalars(
            select(Badge)
            .where(Badge.user_id == current_user.id)
            .order_by(Badge.earned_date.desc())
            .limit(limit)
            .offset(offset)
        ).all()
    )


# ── Conversation history (feeds the AI coach context) ─────────────────────────

@router.post(
    "/conversation",
    response_model=ConversationRead,
    status_code=status.HTTP_201_CREATED,
)
def append_message(
    payload: ConversationIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ConversationMessage:
    """Persist one chat turn (learner or coach) for later context."""
    if payload.role not in CONVERSATION_ROLES:
        raise HTTPException(status_code=422, detail="role must be user or assistant")
    message = ConversationMessage(
        user_id=current_user.id, role=payload.role, content=payload.content
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


@router.get("/conversation", response_model=list[ConversationRead])
def read_conversation(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ConversationMessage]:
    """Read back chat turns in chronological order (oldest first)."""
    return list(
        db.scalars(
            select(ConversationMessage)
            .where(ConversationMessage.user_id == current_user.id)
            .order_by(ConversationMessage.created_at.asc(), ConversationMessage.id.asc())
            .limit(limit)
            .offset(offset)
        ).all()
    )


@router.delete("/conversation")
def clear_conversation(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Delete all stored chat turns for the learner."""
    count = (
        db.query(ConversationMessage)
        .filter(ConversationMessage.user_id == current_user.id)
        .delete(synchronize_session=False)
    )
    db.commit()
    return {"message": f"Cleared {count} stored messages"}


# ── Coach profile reads (lighter contexts for path/personalize/revision) ─────

@router.get("/path-profile")
def get_path_profile(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Profile for learning-path generation: completed + available courses."""
    return path_profile(db, current_user)


@router.get("/personalization-profile")
def get_personalization_profile(
    current_user: User = Depends(get_current_user),
) -> dict:
    """Minimal profile for content personalization."""
    return personalization_profile(current_user)


@router.get("/revision-profile")
def get_revision_profile(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Revision profile: one record per encountered topic."""
    return revision_profile(db, current_user)