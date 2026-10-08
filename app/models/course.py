"""Course, Lesson, Enrollment, and LessonProgress models."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

COURSE_TYPE_FREE = "free"
COURSE_TYPE_PAID = "paid"
COURSE_TYPES = {COURSE_TYPE_FREE, COURSE_TYPE_PAID}

STATUS_DRAFT = "draft"
STATUS_PUBLISHED = "published"
STATUS_ARCHIVED = "archived"
COURSE_STATUSES = {STATUS_DRAFT, STATUS_PUBLISHED, STATUS_ARCHIVED}


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(String(2000), default="")
    difficulty_level: Mapped[str] = mapped_column(String(20), default="beginner")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Marketplace fields (creator MVP). SET NULL, not CASCADE: courses
    # outlive their creator. Deleting a creator detaches their courses (an
    # admin can reassign or remove them) instead of destroying other
    # learners' enrollments and progress.
    creator_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    category: Mapped[str] = mapped_column(String(120), default="")
    outcomes: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    target_audience: Mapped[str] = mapped_column(String(500), default="")
    requirements: Mapped[str] = mapped_column(String(1000), default="")
    thumbnail_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    course_type: Mapped[str] = mapped_column(String(10), default=COURSE_TYPE_FREE)
    price_naira: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default=STATUS_DRAFT)

    lessons: Mapped[list["Lesson"]] = relationship(back_populates="course")
    modules: Mapped[list["CourseModule"]] = relationship(
        back_populates="course", cascade="all, delete-orphan",
        order_by="CourseModule.order",
    )


class CourseModule(Base):
    """A curriculum module grouping lessons within a course."""

    __tablename__ = "course_modules"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    order: Mapped[int] = mapped_column(Integer)

    course: Mapped["Course"] = relationship(back_populates="modules")
    lessons: Mapped[list["Lesson"]] = relationship(back_populates="module")


class Lesson(Base):
    __tablename__ = "lessons"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), index=True)
    module_id: Mapped[int | None] = mapped_column(
        ForeignKey("course_modules.id"), nullable=True, index=True
    )
    order: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(255))
    topic: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(String(2000), default="")
    lesson_type: Mapped[str] = mapped_column(String(30), default="lesson")
    estimated_minutes: Mapped[int] = mapped_column(Integer, default=15)
    content: Mapped[str] = mapped_column(String(10000), default="")
    is_published: Mapped[bool] = mapped_column(Boolean, default=True)

    course: Mapped["Course"] = relationship(back_populates="lessons")
    module: Mapped["CourseModule | None"] = relationship(back_populates="lessons")
    assets: Mapped[list["LessonAsset"]] = relationship(
        back_populates="lesson", cascade="all, delete-orphan"
    )


class Enrollment(Base):
    __tablename__ = "enrollments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), index=True)
    enrolled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(20), default="direct")

    user: Mapped["User"] = relationship(back_populates="enrollments")  # noqa: F821


class LessonProgress(Base):
    __tablename__ = "lesson_progress"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    lesson_id: Mapped[int] = mapped_column(ForeignKey("lessons.id"), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)

    user: Mapped["User"] = relationship(back_populates="lesson_progress")  # noqa: F821