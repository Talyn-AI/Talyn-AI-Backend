"""Creators router: profiles, dashboard, and owned-course listing."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import require_creator
from app.database import get_db
from app.models import AnalyticsEvent, Course, CreatorProfile, Enrollment, Payment, User
from app.schemas.creator import (
    ActivityEntry,
    CreatorDashboard,
    CreatorProfileIn,
    CreatorProfileRead,
)
from app.schemas.analytics import DailyPoint, OverviewResponse

router = APIRouter(prefix="/me/creator", tags=["Creators"])
public_router = APIRouter(prefix="/creators", tags=["Creators"])


@router.put("/profile", response_model=CreatorProfileRead)
def upsert_profile(
    payload: CreatorProfileIn,
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> CreatorProfile:
    """Create or replace the creator's public profile."""
    profile = db.scalar(
        select(CreatorProfile).where(CreatorProfile.user_id == creator.id)
    )
    if profile is None:
        profile = CreatorProfile(user_id=creator.id)
        db.add(profile)
    profile.display_name = payload.display_name
    profile.bio = payload.bio
    profile.image_key = payload.image_key
    # Only when one is actually being set: signup calls this endpoint with a
    # display name and no image, and that must not fail on an absent file.
    if payload.image_key:
        _claim_profile_image(payload.image_key)
    db.commit()
    db.refresh(profile)
    return profile


def _claim_profile_image(key: str) -> None:
    """Verify an uploaded profile image before it goes public.

    A profile image is shown next to every course this creator publishes, so
    an unchecked file here reaches more people than one lesson asset would.
    """
    from app.services import uploads as upload_service
    from app.services.storage import StorageError

    try:
        upload_service.claim(key, "profile_image")
    except upload_service.UploadRejected as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except StorageError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.get("/profile", response_model=CreatorProfileRead)
def get_own_profile(
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> CreatorProfile:
    """Get the creator's own profile."""
    profile = db.scalar(
        select(CreatorProfile).where(CreatorProfile.user_id == creator.id)
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="No creator profile set")
    return profile


@public_router.get("/{user_id}/profile", response_model=CreatorProfileRead)
def get_public_profile(user_id: int, db: Session = Depends(get_db)) -> CreatorProfile:
    """Public creator profile for course pages."""
    profile = db.scalar(
        select(CreatorProfile).where(CreatorProfile.user_id == user_id)
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="Creator profile not found")
    return profile


@router.get("/dashboard", response_model=CreatorDashboard)
def dashboard(
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> CreatorDashboard:
    """Creator stats: courses, learners, revenue, recent activity."""
    course_ids = db.scalars(
        select(Course.id).where(Course.creator_user_id == creator.id)
    ).all()
    published = int(
        db.scalar(
            select(func.count(Course.id)).where(
                Course.creator_user_id == creator.id,
                Course.status == "published",
            )
        )
        or 0
    )
    draft = int(
        db.scalar(
            select(func.count(Course.id)).where(
                Course.creator_user_id == creator.id,
                Course.status == "draft",
            )
        )
        or 0
    )
    learners = 0
    revenue = 0
    recent: list = []
    if course_ids:
        learners = int(
            db.scalar(
                select(func.count(func.distinct(Enrollment.user_id))).where(
                    Enrollment.course_id.in_(course_ids)
                )
            )
            or 0
        )
        revenue = int(
            db.scalar(
                select(func.coalesce(func.sum(Payment.amount_naira), 0)).where(
                    Payment.course_id.in_(course_ids),
                    Payment.status == "success",
                )
            )
            or 0
        )
        rows = db.scalars(
            select(AnalyticsEvent)
            .where(AnalyticsEvent.course_id.in_(course_ids))
            .order_by(AnalyticsEvent.id.desc())
            .limit(8)
        ).all()
        recent = [
            ActivityEntry(
                event=r.event,
                course_id=r.course_id,
                lesson_id=r.lesson_id,
                created_at=r.created_at,
            )
            for r in rows
        ]
    return CreatorDashboard(
        total_courses=len(course_ids),
        published_courses=published,
        draft_courses=draft,
        total_learners=learners,
        total_revenue_naira=revenue,
        recent_activity=recent,
    )


@router.get("/activity", response_model=list[ActivityEntry])
def activity(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> list[ActivityEntry]:
    """Recent analytics events across the creator's own courses."""
    course_ids = db.scalars(
        select(Course.id).where(Course.creator_user_id == creator.id)
    ).all()
    if not course_ids:
        return []
    rows = db.scalars(
        select(AnalyticsEvent)
        .where(AnalyticsEvent.course_id.in_(course_ids))
        .order_by(AnalyticsEvent.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return [
        ActivityEntry(
            event=r.event,
            course_id=r.course_id,
            lesson_id=r.lesson_id,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/analytics/overview", response_model=OverviewResponse)
def analytics_overview(
    days: int = Query(default=30, ge=1, le=90),
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> OverviewResponse:
    """Daily enrollments/purchases/revenue/completions across own courses."""
    from datetime import datetime, time, timedelta, timezone

    course_ids = db.scalars(
        select(Course.id).where(Course.creator_user_id == creator.id)
    ).all()
    points = []
    total_enrollments = total_purchases = total_revenue = total_completions = 0
    active: set[int] = set()
    # Buckets are UTC days, matched as half-open ranges rather than
    # func.date(col) == day. func.date() on a timestamptz casts using the
    # Postgres session timezone, so on a non-UTC host "today" could land in
    # yesterday's bucket; and wrapping the column in a function means the
    # created_at index can never be used.
    today = datetime.now(timezone.utc).date()
    for back in range(days - 1, -1, -1):
        day = today - timedelta(days=back)
        start = datetime.combine(day, time.min, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        enrollments = purchases = revenue = completions = 0
        if course_ids:
            enrollments = int(
                db.scalar(
                    select(func.count(Enrollment.id)).where(
                        Enrollment.course_id.in_(course_ids),
                        Enrollment.enrolled_at >= start,
                        Enrollment.enrolled_at < end,
                    )
                )
                or 0
            )
            row = db.execute(
                select(
                    func.count(Payment.id),
                    func.coalesce(func.sum(Payment.amount_naira), 0),
                ).where(
                    Payment.course_id.in_(course_ids),
                    Payment.status == "success",
                    Payment.created_at >= start,
                    Payment.created_at < end,
                )
            ).one()
            purchases, revenue = int(row[0]), int(row[1])
            completions = int(
                db.scalar(
                    select(func.count(AnalyticsEvent.id)).where(
                        AnalyticsEvent.course_id.in_(course_ids),
                        AnalyticsEvent.event == "course_completed",
                        AnalyticsEvent.created_at >= start,
                        AnalyticsEvent.created_at < end,
                    )
                )
                or 0
            )
            for uid in db.scalars(
                select(Enrollment.user_id).where(
                    Enrollment.course_id.in_(course_ids)
                )
            ).all():
                active.add(uid)
        total_enrollments += enrollments
        total_purchases += purchases
        total_revenue += revenue
        total_completions += completions
        points.append(
            DailyPoint(
                date=day.isoformat(),
                enrollments=enrollments,
                purchases=purchases,
                revenue_naira=revenue,
                completions=completions,
            )
        )
    return OverviewResponse(
        days=days,
        total_enrollments=total_enrollments,
        total_purchases=total_purchases,
        total_revenue_naira=total_revenue,
        total_completions=total_completions,
        active_learners=len(active),
        daily=points,
    )


@router.get("/courses")
def own_courses(
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> list[dict]:
    """The creator's own courses in any status (drafts included)."""
    courses = db.scalars(
        select(Course)
        .where(Course.creator_user_id == creator.id)
        .order_by(Course.id.desc())
    ).all()
    return [
        {"id": c.id, "title": c.title, "status": c.status,
         "course_type": c.course_type, "price_naira": c.price_naira}
        for c in courses
    ]
