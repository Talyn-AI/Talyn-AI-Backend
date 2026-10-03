"""Central event tracking. Callers add events; their own commit persists them."""
import json

from sqlalchemy.orm import Session

from app.models import AnalyticsEvent, User


def track(
    db: Session,
    event: str,
    user: User | None = None,
    course_id: int | None = None,
    lesson_id: int | None = None,
    meta: dict | None = None,
) -> AnalyticsEvent:
    """Record one analytics event in the current transaction."""
    row = AnalyticsEvent(
        user_id=user.id if user is not None else None,
        event=event,
        course_id=course_id,
        lesson_id=lesson_id,
        meta=json.dumps(meta or {}),
    )
    db.add(row)
    return row
