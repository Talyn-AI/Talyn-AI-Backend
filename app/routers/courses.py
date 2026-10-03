"""Courses router: marketplace course management + learner discovery."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.deps import require_creator, can_manage_course, can_read_content, optional_user
from app.database import get_db
from app.models import (
    Course,
    CourseModule,
    Enrollment,
    Lesson,
    LessonAsset,
    LessonProgress,
    LiveSession,
    Payment,
    QuizResult,
    User,
)
from app.models.analytics import (
    COURSE_CREATED,
    COURSE_UPDATED,
    COURSE_VIEWED,
    AnalyticsEvent,
)
from app.models.course import STATUS_PUBLISHED
from app.services.analytics import track
from app.schemas import CourseCreate, CourseListItem, CourseRead, CourseUpdate, LessonRead
from app.schemas.analytics import CourseAnalytics, LessonFunnel, QuizTopicStats

router = APIRouter(prefix="/courses", tags=["Courses"])


@router.post("", response_model=CourseRead, status_code=status.HTTP_201_CREATED)
def create_course(
    payload: CourseCreate,
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> Course:
    """Create a draft course, optionally with its lessons. Creator or admin."""
    if payload.course_type == "paid" and payload.price_naira <= 0:
        raise HTTPException(
            status_code=422, detail="Paid courses require a price_naira above 0"
        )
    course = Course(
        title=payload.title,
        description=payload.description,
        difficulty_level=payload.difficulty_level,
        creator_user_id=creator.id,
        category=payload.category,
        outcomes=payload.outcomes,
        target_audience=payload.target_audience,
        requirements=payload.requirements,
        thumbnail_key=payload.thumbnail_key,
        course_type=payload.course_type,
        price_naira=payload.price_naira,
    )
    for lesson in payload.lessons:
        course.lessons.append(
            Lesson(
                order=lesson.order,
                title=lesson.title,
                topic=lesson.topic,
                lesson_type=lesson.lesson_type,
                estimated_minutes=lesson.estimated_minutes,
                content=lesson.content,
                is_published=lesson.is_published,
            )
        )
    db.add(course)
    db.commit()
    db.refresh(course)
    track(db, COURSE_CREATED, creator, course_id=course.id)
    db.commit()
    return course


@router.get("", response_model=list[CourseListItem])
def list_courses(
    status_filter: str | None = Query(default=None, alias="status"),
    category: str | None = Query(default=None),
    q: str | None = Query(default=None, description="Search title and description"),
    mine: bool = Query(default=False, description="Only my own courses (auth)"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    viewer: User | None = Depends(optional_user),
    db: Session = Depends(get_db),
) -> list[CourseListItem]:
    """Discover courses. Defaults to published; drafts visible to owner/admin."""
    wanted = status_filter or STATUS_PUBLISHED
    if wanted != STATUS_PUBLISHED and not mine:
        # Non-published discovery requires an authenticated viewer; rows are
        # still scoped to owner/admin below.
        if viewer is None:
            raise HTTPException(status_code=401, detail="Not authenticated")

    query = (
        select(Course, func.count(Lesson.id).label("lesson_count"))
        .outerjoin(
            Lesson,
            (Lesson.course_id == Course.id) & (Lesson.is_published.is_(True)),
        )
        .group_by(Course.id)
        .order_by(Course.id)
    )
    if mine:
        if viewer is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        query = query.where(Course.creator_user_id == viewer.id)
        if status_filter:
            query = query.where(Course.status == status_filter)
    else:
        query = query.where(Course.status == wanted)
        if wanted != STATUS_PUBLISHED:
            # viewer is authenticated here (checked above)
            assert viewer is not None
            if not viewer.is_admin:
                query = query.where(Course.creator_user_id == viewer.id)

    if category:
        query = query.where(Course.category.ilike(category))
    if q:
        like = f"%{q}%"
        query = query.where(
            Course.title.ilike(like) | Course.description.ilike(like)
        )
    rows = db.execute(query.limit(limit).offset(offset)).all()
    return [
        CourseListItem(
            id=c.id,
            title=c.title,
            description=c.description,
            difficulty_level=c.difficulty_level,
            category=c.category,
            course_type=c.course_type,
            price_naira=c.price_naira,
            status=c.status,
            lesson_count=int(count),
        )
        for c, count in rows
    ]


@router.get("/{course_id}", response_model=CourseRead)
def get_course(
    course_id: int,
    viewer: User | None = Depends(optional_user),
    db: Session = Depends(get_db),
) -> Course:
    """Get a course with its lessons. Drafts visible to owner/admin only."""
    course = db.scalar(
        select(Course)
        .where(Course.id == course_id)
        .options(
            selectinload(Course.lessons),
            selectinload(Course.modules).selectinload(CourseModule.lessons),
        )
    )
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    if course.status != STATUS_PUBLISHED:
        if viewer is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        if not can_manage_course(course, viewer):
            raise HTTPException(status_code=403, detail="Not your course")
    track(db, COURSE_VIEWED, viewer, course_id=course.id)
    db.commit()
    # Strip AFTER committing: these in-memory nulls must never persist.
    if not can_read_content(db, viewer, course):
        for lesson in course.lessons:
            lesson.content = None  # type: ignore[assignment]
        for module in course.modules:
            for lesson in module.lessons:
                lesson.content = None  # type: ignore[assignment]
    return course


@router.patch("/{course_id}", response_model=CourseRead)
def update_course(
    course_id: int,
    payload: CourseUpdate,
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> Course:
    """Edit a course. Owner or admin (status changes go through publish flow)."""
    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    if not can_manage_course(course, creator):
        raise HTTPException(status_code=403, detail="Not your course")

    data = payload.model_dump(exclude_unset=True)

    # A thumbnail is not a lesson asset, so it never passes through the attach
    # endpoint. Verify it here or an unchecked file ends up on every discovery
    # page. Keyed off "was this field actually sent", so leaving it out of the
    # PATCH does not re-verify the existing thumbnail.
    if "thumbnail_key" in data and data["thumbnail_key"]:
        _claim_thumbnail(data["thumbnail_key"])

    new_type = data.get("course_type", course.course_type)
    new_price = data.get("price_naira", course.price_naira)
    if new_type == "paid" and new_price <= 0:
        raise HTTPException(
            status_code=422, detail="Paid courses require a price_naira above 0"
        )
    for field, value in data.items():
        setattr(course, field, value)
    track(db, COURSE_UPDATED, creator, course_id=course.id)
    db.commit()
    db.refresh(course)
    return course


def _claim_thumbnail(key: str) -> None:
    """Verify an uploaded thumbnail before it is attached to a course."""
    from app.services import uploads as upload_service
    from app.services.storage import StorageError

    try:
        upload_service.claim(key, "thumbnail")
    except upload_service.UploadRejected as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except StorageError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.get("/{course_id}/lessons", response_model=list[LessonRead])
def list_lessons(
    course_id: int,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    viewer: User | None = Depends(optional_user),
    db: Session = Depends(get_db),
) -> list[Lesson]:
    """List published lessons (structure always; content gated for paid)."""
    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    if course.status != STATUS_PUBLISHED:
        if viewer is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        if not can_manage_course(course, viewer):
            raise HTTPException(status_code=403, detail="Not your course")
    lessons = db.scalars(
        select(Lesson)
        .where(Lesson.course_id == course_id, Lesson.is_published.is_(True))
        .order_by(Lesson.order)
        .limit(limit)
        .offset(offset)
    ).all()
    if not can_read_content(db, viewer, course):
        for lesson in lessons:
            lesson.content = None  # type: ignore[assignment]
    return list(lessons)


@router.delete("/{course_id}")
def delete_course(
    course_id: int,
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> dict:
    """Delete a course with its lessons, progress, quiz results, enrollments."""
    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    if not can_manage_course(course, creator):
        raise HTTPException(status_code=403, detail="Not your course")

    lesson_ids = select(Lesson.id).where(Lesson.course_id == course_id)
    sales = int(
        db.scalar(
            select(func.count(Payment.id)).where(
                Payment.course_id == course_id,
                Payment.status == "success",
            )
        )
        or 0
    )
    if sales:
        raise HTTPException(
            status_code=409,
            detail="Course has recorded sales; archive it instead of deleting",
        )
    db.query(Payment).filter(Payment.course_id == course_id).delete(
        synchronize_session=False
    )
    from app.models.analytics import AnalyticsEvent

    db.query(AnalyticsEvent).filter(
        AnalyticsEvent.course_id == course_id
    ).delete(synchronize_session=False)
    db.query(LessonAsset).filter(
        LessonAsset.lesson_id.in_(lesson_ids)
    ).delete(synchronize_session=False)
    db.query(LessonProgress).filter(
        LessonProgress.lesson_id.in_(lesson_ids)
    ).delete(synchronize_session=False)
    db.query(QuizResult).filter(QuizResult.course_id == course_id).delete(
        synchronize_session=False
    )
    db.query(Enrollment).filter(Enrollment.course_id == course_id).delete(
        synchronize_session=False
    )
    db.query(LiveSession).filter(LiveSession.course_id == course_id).delete(
        synchronize_session=False
    )
    db.query(Lesson).filter(Lesson.course_id == course_id).delete(
        synchronize_session=False
    )
    db.query(CourseModule).filter(CourseModule.course_id == course_id).delete(
        synchronize_session=False
    )
    db.delete(course)
    db.commit()
    return {"message": f"Course {course_id} and its content deleted"}


@router.get("/{course_id}/analytics", response_model=CourseAnalytics)
def course_analytics(
    course_id: int,
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> CourseAnalytics:
    """Lesson funnel + quiz aggregates for a course. Owner or admin."""
    from app.models.analytics import (
        COURSE_COMPLETED,
        LESSON_STARTED,
        LESSON_VIEWED,
    )

    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    if not can_manage_course(course, creator):
        raise HTTPException(status_code=403, detail="Not your course")

    lessons = db.scalars(
        select(Lesson)
        .where(Lesson.course_id == course.id)
        .order_by(Lesson.order)
    ).all()
    lesson_ids = [lesson.id for lesson in lessons]

    views: dict[int, int] = {}
    starts: dict[int, int] = {}
    if lesson_ids:
        for lesson_id, event, count in db.execute(
            select(
                AnalyticsEvent.lesson_id,
                AnalyticsEvent.event,
                func.count(AnalyticsEvent.id),
            )
            .where(
                AnalyticsEvent.course_id == course.id,
                AnalyticsEvent.lesson_id.in_(lesson_ids),
                AnalyticsEvent.event.in_(
                    [LESSON_VIEWED, LESSON_STARTED]
                ),
            )
            .group_by(AnalyticsEvent.lesson_id, AnalyticsEvent.event)
        ).all():
            (views if event == LESSON_VIEWED else starts)[lesson_id] = int(count)

    completions: dict[int, int] = {}
    if lesson_ids:
        for lesson_id, count in db.execute(
            select(LessonProgress.lesson_id, func.count(LessonProgress.id))
            .where(
                LessonProgress.lesson_id.in_(lesson_ids),
                LessonProgress.completed_at.is_not(None),
            )
            .group_by(LessonProgress.lesson_id)
        ).all():
            completions[lesson_id] = int(count)

    funnel = [
        LessonFunnel(
            lesson_id=lesson.id,
            title=lesson.title,
            order=lesson.order,
            views=views.get(lesson.id, 0),
            starts=starts.get(lesson.id, 0),
            completions=completions.get(lesson.id, 0),
            completion_rate=round(
                completions.get(lesson.id, 0) / max(starts.get(lesson.id, 0), 1), 3
            ),
        )
        for lesson in lessons
    ]

    quiz_rows = db.execute(
        select(
            QuizResult.topic,
            func.sum(QuizResult.attempts).label("attempts"),
            func.avg(QuizResult.score_percent).label("avg"),
            func.max(QuizResult.score_percent).label("best"),
        )
        .where(QuizResult.course_id == course.id)
        .group_by(QuizResult.topic)
        .order_by(QuizResult.topic)
    ).all()

    return CourseAnalytics(
        course_id=course.id,
        lessons=funnel,
        quiz_topics=[
            QuizTopicStats(
                topic=r.topic,
                attempts=int(r.attempts),
                avg_score=round(float(r.avg), 1),
                best_score=round(float(r.best), 1),
            )
            for r in quiz_rows
        ],
        total_views=sum(views.values()),
        total_enrollments=int(
            db.scalar(
                select(func.count(Enrollment.id)).where(
                    Enrollment.course_id == course.id
                )
            )
            or 0
        ),
        total_completions=int(
            db.scalar(
                select(func.count(AnalyticsEvent.id)).where(
                    AnalyticsEvent.course_id == course.id,
                    AnalyticsEvent.event == COURSE_COMPLETED,
                )
            )
            or 0
        ),
    )
