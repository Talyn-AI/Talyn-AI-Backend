"""AnalyticsEvent: central event tracking for creator + learner activity."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Creator events
CREATOR_SIGNED_UP = "creator_signed_up"
CREATOR_LOGGED_IN = "creator_logged_in"
COURSE_CREATED = "course_created"
COURSE_UPDATED = "course_updated"
MODULE_CREATED = "module_created"
LESSON_CREATED = "lesson_created"
CONTENT_UPLOADED = "content_uploaded"
COURSE_PREVIEWED = "course_previewed"
COURSE_PUBLISHED = "course_published"
COURSE_UNPUBLISHED = "course_unpublished"

# Learner events
COURSE_VIEWED = "course_viewed"
LESSON_VIEWED = "lesson_viewed"
COURSE_PURCHASED = "course_purchased"
COURSE_ENROLLED = "course_enrolled"
LESSON_STARTED = "lesson_started"
LESSON_COMPLETED = "lesson_completed"
COURSE_COMPLETED = "course_completed"

# Social events
COMMUNITY_POST_CREATED = "community_post_created"
LIVE_SESSION_SCHEDULED = "live_scheduled"
LIVE_SESSION_STARTED = "live_started"
LIVE_SESSION_ENDED = "live_ended"


class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    event: Mapped[str] = mapped_column(String(50), index=True)
    course_id: Mapped[int | None] = mapped_column(
        ForeignKey("courses.id"), nullable=True, index=True
    )
    lesson_id: Mapped[int | None] = mapped_column(
        ForeignKey("lessons.id"), nullable=True, index=True
    )
    meta: Mapped[str] = mapped_column(String(2000), default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
