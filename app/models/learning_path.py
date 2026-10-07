"""LearningPath: a learner's saved ordering over published courses."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class LearningPath(Base):
    """One named path. Progress is derived from enrollments, not stored —
    a second copy of "done" would eventually disagree with the first."""

    __tablename__ = "learning_paths"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(1000), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    steps: Mapped[list["LearningPathStep"]] = relationship(
        back_populates="path",
        cascade="all, delete-orphan",
        order_by="LearningPathStep.position",
    )


class LearningPathStep(Base):
    """One course in a path, at a position. Courses may repeat across paths
    but not within one — the create/update endpoints dedupe."""

    __tablename__ = "learning_path_steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    path_id: Mapped[int] = mapped_column(
        ForeignKey("learning_paths.id", ondelete="CASCADE"), index=True
    )
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column(Integer, default=0)

    path: Mapped["LearningPath"] = relationship(back_populates="steps")
