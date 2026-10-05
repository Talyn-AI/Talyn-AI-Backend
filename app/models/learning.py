"""Learning analytics models: quiz results, XP events, badges, study plans."""
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class QuizResult(Base):
    __tablename__ = "quiz_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    course_id: Mapped[int | None] = mapped_column(
        ForeignKey("courses.id"), nullable=True, index=True
    )
    # Which quiz this was. Nullable for attempts that predate the link; the
    # quiz gate only credits results that carry one.
    lesson_id: Mapped[int | None] = mapped_column(
        ForeignKey("lessons.id"), nullable=True, index=True
    )
    topic: Mapped[str] = mapped_column(String(255))
    score_percent: Mapped[float] = mapped_column(Float)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    taken_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    user: Mapped["User"] = relationship(back_populates="quiz_results")  # noqa: F821


class XpEvent(Base):
    """A single XP award event, feeding xp_total / xp_breakdown / level."""

    __tablename__ = "xp_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    activity: Mapped[str] = mapped_column(String(30))  # XpActivityType value
    amount: Mapped[int] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    earned_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    user: Mapped["User"] = relationship(back_populates="xp_events")  # noqa: F821


class Badge(Base):
    __tablename__ = "badges"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    badge_id: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(500), default="")
    icon: Mapped[str | None] = mapped_column(String(50), nullable=True)
    earned_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="badges")  # noqa: F821


class StudyPlan(Base):
    """One study plan per learner (simple 1:1 for now)."""

    __tablename__ = "study_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    daily_goal_minutes: Mapped[int] = mapped_column(Integer, default=30)
    weekly_target_lessons: Mapped[int] = mapped_column(Integer, default=3)
    focus_topics: Mapped[str] = mapped_column(String(1000), default="[]")
    deadline: Mapped[str | None] = mapped_column(String(50), nullable=True)