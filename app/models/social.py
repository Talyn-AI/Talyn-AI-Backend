"""Social models: community posts/replies, direct messages, live sessions."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class CommunityPost(Base):
    __tablename__ = "community_posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    author_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(String(5000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    replies: Mapped[list["CommunityReply"]] = relationship(
        back_populates="post", cascade="all, delete-orphan",
        order_by="CommunityReply.id",
    )


class CommunityReply(Base):
    __tablename__ = "community_replies"

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("community_posts.id", ondelete="CASCADE"), index=True)
    author_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    body: Mapped[str] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    post: Mapped["CommunityPost"] = relationship(back_populates="replies")


class DirectMessage(Base):
    __tablename__ = "direct_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    recipient_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    body: Mapped[str] = mapped_column(String(2000))
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


LIVE_SCHEDULED = "scheduled"
LIVE_LIVE = "live"
LIVE_ENDED = "ended"
LIVE_CANCELLED = "cancelled"
LIVE_STATUSES = {LIVE_SCHEDULED, LIVE_LIVE, LIVE_ENDED, LIVE_CANCELLED}


class LiveSession(Base):
    __tablename__ = "live_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60)
    meeting_url: Mapped[str] = mapped_column(String(2000), default="")
    status: Mapped[str] = mapped_column(String(20), default=LIVE_SCHEDULED)
    is_recorded: Mapped[bool] = mapped_column(Boolean, default=False)
