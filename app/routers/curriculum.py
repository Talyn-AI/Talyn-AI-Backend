"""Curriculum router: modules, lesson management, publish workflow, preview."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.deps import can_manage_course, can_read_content, optional_user, require_creator
from app.database import get_db
from app.models import Course, CourseModule, Lesson, LessonAsset, LessonProgress, User
from app.models.analytics import (
    AnalyticsEvent,
    CONTENT_UPLOADED,
    COURSE_PREVIEWED,
    COURSE_PUBLISHED,
    COURSE_UNPUBLISHED,
    LESSON_CREATED,
    LESSON_VIEWED,
    MODULE_CREATED,
)
from app.models.course import STATUS_ARCHIVED, STATUS_DRAFT, STATUS_PUBLISHED
from app.services.analytics import track
from app.schemas import (
    CourseRead,
    LessonCreateIn,
    LessonRead,
    LessonUpdate,
    ModuleCreate,
    ModuleRead,
    ModuleUpdate,
    PublishCheck,
    ReorderIn,
)

router = APIRouter(prefix="/courses", tags=["Curriculum"])
lessons_router = APIRouter(prefix="/lessons", tags=["Curriculum"])


def _owned_course(db: Session, user: User, course_id: int) -> Course:
    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    if not can_manage_course(course, user):
        raise HTTPException(status_code=403, detail="Not your course")
    return course


def _course_module(db: Session, course: Course, module_id: int) -> CourseModule:
    module = db.scalar(
        select(CourseModule).where(
            CourseModule.id == module_id, CourseModule.course_id == course.id
        )
    )
    if module is None:
        raise HTTPException(status_code=404, detail="Module not found in this course")
    return module


def _next_order(db: Session, course_id: int, module_id: int | None) -> int:
    query = select(func.coalesce(func.max(Lesson.order), 0)).where(
        Lesson.course_id == course_id
    )
    if module_id is None:
        query = query.where(Lesson.module_id.is_(None))
    else:
        query = query.where(Lesson.module_id == module_id)
    return int(db.scalar(query) or 0) + 1


# ── Modules ───────────────────────────────────────────────────────────────────

@router.post(
    "/{course_id}/modules",
    response_model=ModuleRead,
    status_code=status.HTTP_201_CREATED,
)
def create_module(
    course_id: int,
    payload: ModuleCreate,
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> CourseModule:
    """Append a module to the end of the course curriculum."""
    course = _owned_course(db, creator, course_id)
    order = int(
        db.scalar(
            select(func.coalesce(func.max(CourseModule.order), 0)).where(
                CourseModule.course_id == course.id
            )
        )
        or 0
    )
    module = CourseModule(course_id=course.id, title=payload.title, order=order + 1)
    db.add(module)
    db.commit()
    db.refresh(module)
    track(db, MODULE_CREATED, creator, course_id=course.id)
    db.commit()
    return module


@router.patch("/{course_id}/modules/{module_id}", response_model=ModuleRead)
def rename_module(
    course_id: int,
    module_id: int,
    payload: ModuleUpdate,
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> CourseModule:
    """Rename a module."""
    course = _owned_course(db, creator, course_id)
    module = _course_module(db, course, module_id)
    if payload.title is not None:
        module.title = payload.title
    db.commit()
    db.refresh(module)
    return module


@router.delete("/{course_id}/modules/{module_id}")
def delete_module(
    course_id: int,
    module_id: int,
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> dict:
    """Delete a module; its lessons become unassigned (not deleted)."""
    course = _owned_course(db, creator, course_id)
    module = _course_module(db, course, module_id)
    db.query(Lesson).filter(Lesson.module_id == module.id).update(
        {Lesson.module_id: None}, synchronize_session=False
    )
    db.delete(module)
    db.commit()
    return {"message": f"Module {module_id} deleted, lessons unassigned"}


@router.put("/{course_id}/modules/reorder")
def reorder_modules(
    course_id: int,
    payload: ReorderIn,
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> dict:
    """Set module order; ordered_ids must match the course's modules exactly."""
    course = _owned_course(db, creator, course_id)
    existing = db.scalars(
        select(CourseModule.id).where(CourseModule.course_id == course.id)
    ).all()
    if set(payload.ordered_ids) != set(existing) or len(payload.ordered_ids) != len(existing):
        raise HTTPException(
            status_code=422,
            detail="ordered_ids must contain exactly the course's module ids",
        )
    for position, module_id in enumerate(payload.ordered_ids, start=1):
        db.query(CourseModule).filter(CourseModule.id == module_id).update(
            {CourseModule.order: position}, synchronize_session=False
        )
    db.commit()
    return {"message": f"Reordered {len(existing)} modules"}


# ── Lessons ───────────────────────────────────────────────────────────────────

@router.post(
    "/{course_id}/lessons",
    response_model=LessonRead,
    status_code=status.HTTP_201_CREATED,
)
def create_lesson(
    course_id: int,
    payload: LessonCreateIn,
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> Lesson:
    """Add a lesson to a module (or the unassigned pool); order defaults to last."""
    course = _owned_course(db, creator, course_id)
    if payload.module_id is not None:
        _course_module(db, course, payload.module_id)
    lesson = Lesson(
        course_id=course.id,
        module_id=payload.module_id,
        order=payload.order or _next_order(db, course.id, payload.module_id),
        title=payload.title,
        topic=payload.topic,
        description=payload.description,
        lesson_type=payload.lesson_type,
        estimated_minutes=payload.estimated_minutes,
        content=payload.content,
        is_published=payload.is_published,
    )
    db.add(lesson)
    db.commit()
    db.refresh(lesson)
    track(db, LESSON_CREATED, creator, course_id=course.id,
                lesson_id=lesson.id)
    db.commit()
    return lesson


@router.put("/{course_id}/lessons/reorder")
def reorder_lessons(
    course_id: int,
    payload: ReorderIn,
    module_id: int | None = None,
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> dict:
    """Order lessons within a module (or the unassigned pool when omitted)."""
    course = _owned_course(db, creator, course_id)
    if module_id is not None:
        _course_module(db, course, module_id)
        scope = Lesson.module_id == module_id
    else:
        scope = Lesson.module_id.is_(None)
    existing = db.scalars(
        select(Lesson.id).where(Lesson.course_id == course.id, scope)
    ).all()
    if set(payload.ordered_ids) != set(existing) or len(payload.ordered_ids) != len(existing):
        raise HTTPException(
            status_code=422,
            detail="ordered_ids must contain exactly the lessons in scope",
        )
    for position, lesson_id in enumerate(payload.ordered_ids, start=1):
        db.query(Lesson).filter(Lesson.id == lesson_id).update(
            {Lesson.order: position}, synchronize_session=False
        )
    db.commit()
    return {"message": f"Reordered {len(existing)} lessons"}


@lessons_router.patch("/{lesson_id}", response_model=LessonRead)
def update_lesson(
    lesson_id: int,
    payload: LessonUpdate,
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> Lesson:
    """Edit a lesson; module moves must stay inside the same course."""
    lesson = db.get(Lesson, lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    course = db.get(Course, lesson.course_id)
    if course is None or not can_manage_course(course, creator):
        raise HTTPException(status_code=403, detail="Not your course")

    data = payload.model_dump(exclude_unset=True)
    if "module_id" in data and data["module_id"] is not None:
        _course_module(db, course, data["module_id"])
    for field, value in data.items():
        setattr(lesson, field, value)
    db.commit()
    db.refresh(lesson)
    return lesson


@lessons_router.delete("/{lesson_id}")
def delete_lesson(
    lesson_id: int,
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> dict:
    """Delete a lesson with its learner progress rows."""
    lesson = db.get(Lesson, lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    course = db.get(Course, lesson.course_id)
    if course is None or not can_manage_course(course, creator):
        raise HTTPException(status_code=403, detail="Not your course")

    db.query(LessonProgress).filter(
        LessonProgress.lesson_id == lesson.id
    ).delete(synchronize_session=False)
    db.query(AnalyticsEvent).filter(
        AnalyticsEvent.lesson_id == lesson.id
    ).delete(synchronize_session=False)
    db.delete(lesson)
    db.commit()
    return {"message": f"Lesson {lesson_id} deleted"}


@lessons_router.get("/{lesson_id}", response_model=LessonRead)
def get_lesson(
    lesson_id: int,
    viewer: User | None = Depends(optional_user),
    db: Session = Depends(get_db),
) -> Lesson:
    """Lesson detail. Structure is public for published courses; paid
    content needs a purchase enrollment (owner/admin always pass)."""
    lesson = db.get(Lesson, lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    course = db.get(Course, lesson.course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    if course.status != STATUS_PUBLISHED:
        if viewer is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        if not can_manage_course(course, viewer):
            raise HTTPException(status_code=403, detail="Not your course")
    track(db, LESSON_VIEWED, viewer, course_id=course.id, lesson_id=lesson.id)
    db.commit()
    # Strip AFTER committing: the in-memory null must never persist.
    if not can_read_content(db, viewer, course):
        lesson.content = None  # type: ignore[assignment]
    return lesson


# ── Publish workflow ──────────────────────────────────────────────────────────

def validate_for_publish(db: Session, course: Course) -> list[str]:
    """PRD 5.10 checks. Returns error strings; empty means publishable."""
    errors = []
    if not course.title.strip():
        errors.append("Title is required")
    if not course.description.strip():
        errors.append("Description is required")
    if not course.thumbnail_key:
        errors.append("Thumbnail is required")
    if not course.category.strip():
        errors.append("Category is required")
    if course.difficulty_level not in ("beginner", "intermediate", "advanced"):
        errors.append("Level must be beginner, intermediate, or advanced")
    if not course.outcomes:
        errors.append("At least one learning outcome is required")
    if not course.target_audience.strip():
        errors.append("Target audience is required")
    if course.course_type == "paid" and course.price_naira <= 0:
        errors.append("Paid courses require a price above 0")

    modules = db.scalars(
        select(CourseModule)
        .where(CourseModule.course_id == course.id)
        .order_by(CourseModule.order)
        .options(selectinload(CourseModule.lessons))
    ).all()
    if not modules:
        errors.append("At least one module is required")
    video_lesson_ids = set(
        db.scalars(
            select(LessonAsset.lesson_id).where(LessonAsset.kind == "video")
        ).all()
    )
    total_lessons = 0
    for module in modules:
        if not module.lessons:
            errors.append(f"Module '{module.title}' has no lessons")
            continue
        total_lessons += len(module.lessons)
        for lesson in module.lessons:
            if not lesson.content.strip() and lesson.id not in video_lesson_ids:
                errors.append(
                    f"Lesson '{lesson.title}' needs content or an uploaded video"
                )
    if modules and total_lessons == 0:
        errors.append("At least one lesson is required")
    return errors


def _load_for_read(db: Session, course_id: int) -> Course:
    course = db.scalar(
        select(Course)
        .where(Course.id == course_id)
        .options(
            selectinload(Course.lessons),
            selectinload(Course.modules).selectinload(CourseModule.lessons),
        )
    )
    assert course is not None
    return course


@router.get("/{course_id}/publish-check", response_model=PublishCheck)
def publish_check(
    course_id: int,
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> PublishCheck:
    """Dry-run the publish validation (powers UI validation states)."""
    course = _owned_course(db, creator, course_id)
    errors = validate_for_publish(db, course)
    return PublishCheck(publishable=not errors, errors=errors)


@router.post("/{course_id}/publish", response_model=CourseRead)
def publish_course(
    course_id: int,
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> Course:
    """Publish a course after validation; invalid courses get 422 + errors."""
    course = _owned_course(db, creator, course_id)
    errors = validate_for_publish(db, course)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})
    course.status = STATUS_PUBLISHED
    track(db, COURSE_PUBLISHED, creator, course_id=course.id)
    db.commit()
    return _load_for_read(db, course.id)


@router.post("/{course_id}/unpublish", response_model=CourseRead)
def unpublish_course(
    course_id: int,
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> Course:
    """Return a course to draft (hides it from discovery)."""
    course = _owned_course(db, creator, course_id)
    course.status = STATUS_DRAFT
    track(db, COURSE_UNPUBLISHED, creator, course_id=course.id)
    db.commit()
    return _load_for_read(db, course.id)


@router.post("/{course_id}/archive", response_model=CourseRead)
def archive_course(
    course_id: int,
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> Course:
    """Archive a course (hidden from discovery, kept for records)."""
    course = _owned_course(db, creator, course_id)
    course.status = STATUS_ARCHIVED
    db.commit()
    return _load_for_read(db, course.id)


@router.get("/{course_id}/preview", response_model=CourseRead)
def preview_course(
    course_id: int,
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> Course:
    """Student-view preview of a course (works on drafts; editing resumes after)."""
    course = _owned_course(db, creator, course_id)
    track(db, COURSE_PREVIEWED, creator, course_id=course.id)
    db.commit()
    return _load_for_read(db, course.id)
