"""Document analysis and generated study schedules.

A learner uploads a document, the AI reads it, and two artifacts come out at
two different prices:

- MaterialAnalysis is free: topics, objectives, and a duration estimate —
  the "here's what we found" preview shown before the paywall.
- StudySchedule is the paid artifact: a day-by-day plan generated only
  after a successful payment, yours permanently once unlocked.

Progress lives on the days (completed_at), never as a second copy of the
schedule itself.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

SCHEDULE_DAYS = 14


class MaterialAnalysis(Base):
    """The free preview of what a document contains. One row per material,
    replaced when the learner asks for a fresh analysis."""

    __tablename__ = "material_analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int] = mapped_column(
        ForeignKey("learner_materials.id", ondelete="CASCADE"),
        unique=True,
    )
    topics: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    objectives: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    estimated_minutes: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str] = mapped_column(Text, default="")
    # Learner intent, chosen after the preview: why this document, and in
    # how many days. Generation reads both. NULL timeline means "not
    # chosen" — deliberately not defaulted, so skipping the step is visible.
    purpose: Mapped[str] = mapped_column(String(100), default="")
    timeline_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class StudySchedule(Base):
    """The unlocked 14-day plan for one material. Created lazily on first
    read after payment, never before — generating schedules for abandoned
    checkouts would burn AI calls for nothing."""

    __tablename__ = "study_schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    material_id: Mapped[int] = mapped_column(
        ForeignKey("learner_materials.id", ondelete="CASCADE"),
        unique=True,
    )
    title: Mapped[str] = mapped_column(String(255), default="")
    # Echoed from the analysis at generation time: why this plan exists.
    purpose: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    days: Mapped[list["ScheduleDay"]] = relationship(
        back_populates="schedule",
        cascade="all, delete-orphan",
        order_by="ScheduleDay.day_number",
    )


class ScheduleDay(Base):
    """One day of the plan. Completion is a timestamp, not a boolean, so a
    streak or "finished in N days" view is answerable later without a new
    column."""

    __tablename__ = "schedule_days"

    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int] = mapped_column(
        ForeignKey("study_schedules.id", ondelete="CASCADE"), index=True
    )
    day_number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(255), default="")
    objectives: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    tasks: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    schedule: Mapped["StudySchedule"] = relationship(back_populates="days")
