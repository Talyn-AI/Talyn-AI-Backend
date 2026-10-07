"""Learning paths: a learner's saved ordering over published courses.

Creating a path references courses; it never enrolls — enrolment carries
payment and access meaning, and conflating "I plan to take this" with "I
have paid for this" would be a billing bug. Progress is derived from
enrollments at read time, never stored.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, require_onboarding
from app.database import get_db
from app.models import Course, Enrollment, LearningPath, LearningPathStep, Lesson, User
from app.models.course import STATUS_PUBLISHED
from app.schemas import PathCreateIn, PathRead, PathStepRead, PathUpdateIn

router = APIRouter(prefix="/me/paths", tags=["Learning Paths"])


def _owned_path(db: Session, user: User, path_id: int) -> LearningPath:
    """The learner's own path, or 404. Another learner's paths are not
    confirmed to exist — 404, not 403."""
    path = db.scalar(
        select(LearningPath).where(
            LearningPath.id == path_id, LearningPath.user_id == user.id
        )
    )
    if path is None:
        raise HTTPException(status_code=404, detail="Learning path not found")
    return path


def _resolve_courses(db: Session, course_ids: list[int]) -> list[Course]:
    """Dedupe preserving order, then require every id to be published.

    Drafts are rejected rather than skipped: silently dropping a course the
    learner explicitly chose would show them a path missing something with no
    explanation of where it went.
    """
    ordered = list(dict.fromkeys(course_ids))
    courses = db.scalars(
        select(Course).where(Course.id.in_(ordered))
    ).all() if ordered else []
    by_id = {c.id: c for c in courses}
    missing = [cid for cid in ordered if cid not in by_id]
    if missing:
        raise HTTPException(
            status_code=404, detail=f"Course {missing[0]} not found"
        )
    unpublished = [by_id[cid] for cid in ordered
                   if by_id[cid].status != STATUS_PUBLISHED]
    if unpublished:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"\"{unpublished[0].title}\" is not published yet",
        )
    return [by_id[cid] for cid in ordered]


def _read_path(db: Session, user: User, path: LearningPath) -> PathRead:
    """Assemble the response: steps in order with enrollment-derived status."""
    steps = db.scalars(
        select(LearningPathStep)
        .where(LearningPathStep.path_id == path.id)
        .order_by(LearningPathStep.position, LearningPathStep.id)
    ).all()
    course_ids = [s.course_id for s in steps]

    courses = {
        c.id: c
        for c in db.scalars(
            select(Course).where(Course.id.in_(course_ids))
        ).all()
    } if course_ids else {}
    # A course deleted after being added drops its step through the CASCADE,
    # so anything here still exists — no defensive skipping needed.
    enrollments = {
        e.course_id: e
        for e in db.scalars(
            select(Enrollment).where(
                Enrollment.user_id == user.id,
                Enrollment.course_id.in_(course_ids),
            )
        ).all()
    } if course_ids else {}
    lesson_counts = dict(
        db.execute(
            select(Lesson.course_id, func.count(Lesson.id))
            .where(
                Lesson.course_id.in_(course_ids),
                Lesson.is_published.is_(True),
            )
            .group_by(Lesson.course_id)
        ).all()
    ) if course_ids else {}

    step_reads = []
    completed = 0
    for position, step in enumerate(steps):
        course = courses[step.course_id]
        enrollment = enrollments.get(step.course_id)
        if enrollment is not None and enrollment.completed:
            step_status = "completed"
            completed += 1
        elif enrollment is not None:
            step_status = "in_progress"
        else:
            step_status = "not_started"
        step_reads.append(PathStepRead(
            position=position,
            course_id=course.id,
            title=course.title,
            difficulty_level=course.difficulty_level,
            lessons_total=lesson_counts.get(course.id, 0),
            status=step_status,
        ))

    total = len(step_reads)
    return PathRead(
        id=path.id,
        title=path.title,
        description=path.description,
        steps=step_reads,
        courses_total=total,
        courses_completed=completed,
        completion_percent=round(completed / total * 100, 1) if total else 0.0,
        created_at=path.created_at,
        updated_at=path.updated_at,
    )


def _replace_steps(db: Session, path: LearningPath, courses: list[Course]) -> None:
    """Swap the step list wholesale. Delete-then-insert keeps positions
    dense with no compaction pass, and the list is capped at 20."""
    db.query(LearningPathStep).filter(
        LearningPathStep.path_id == path.id
    ).delete(synchronize_session=False)
    for position, course in enumerate(courses):
        db.add(LearningPathStep(
            path_id=path.id, course_id=course.id, position=position
        ))


@router.post("", response_model=PathRead, status_code=status.HTTP_201_CREATED)
def create_path(
    payload: PathCreateIn,
    current_user: User = Depends(require_onboarding),
    db: Session = Depends(get_db),
) -> PathRead:
    """Save an ordered path over published courses."""
    path = LearningPath(
        user_id=current_user.id,
        title=payload.title.strip(),
        description=payload.description.strip(),
    )
    db.add(path)
    db.flush()
    _replace_steps(db, path, _resolve_courses(db, payload.course_ids))
    db.commit()
    db.refresh(path)
    return _read_path(db, current_user, path)


@router.get("", response_model=list[PathRead])
def list_paths(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PathRead]:
    """The learner's own paths, newest first, each with progress."""
    paths = db.scalars(
        select(LearningPath)
        .where(LearningPath.user_id == current_user.id)
        .order_by(LearningPath.id.desc())
    ).all()
    return [_read_path(db, current_user, path) for path in paths]


@router.get("/{path_id}", response_model=PathRead)
def get_path(
    path_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PathRead:
    """One path with per-step status."""
    return _read_path(db, current_user, _owned_path(db, current_user, path_id))


@router.put("/{path_id}", response_model=PathRead)
def update_path(
    path_id: int,
    payload: PathUpdateIn,
    current_user: User = Depends(require_onboarding),
    db: Session = Depends(get_db),
) -> PathRead:
    """Rename, re-describe, or replace the course list wholesale."""
    path = _owned_path(db, current_user, path_id)
    if payload.title is not None:
        path.title = payload.title.strip()
    if payload.description is not None:
        path.description = payload.description.strip()
    if payload.course_ids is not None:
        _replace_steps(db, path, _resolve_courses(db, payload.course_ids))
    db.commit()
    db.refresh(path)
    return _read_path(db, current_user, path)


@router.delete("/{path_id}")
def delete_path(
    path_id: int,
    current_user: User = Depends(require_onboarding),
    db: Session = Depends(get_db),
) -> dict:
    """Delete a path. Steps go with it through the CASCADE; enrollments and
    progress are untouched — a plan is not the work."""
    path = _owned_path(db, current_user, path_id)
    db.delete(path)
    db.commit()
    return {"message": f"Learning path {path_id} deleted"}
