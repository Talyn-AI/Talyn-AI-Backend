"""BuddyMatch model — a connection between two learners."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class BuddyMatch(Base):
    __tablename__ = "buddy_matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)  # requesting learner
    buddy_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    match_score: Mapped[int] = mapped_column(Integer, default=0)  # 0-100
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | accepted | declined
    matched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )