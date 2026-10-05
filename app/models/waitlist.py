"""Waitlist signup model."""
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

WAITLIST_ROLES = {"learner", "creator"}


class WaitlistSignup(Base):
    """One person asking to be told when Talyn opens.

    No user_id: these are people who have not signed up yet, and requiring an
    account to join a waiting list defeats the page that collects them.
    """

    __tablename__ = "waitlist_signups"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20))
    interests: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    # Free text for "can't find your preferred course? name it here" — the one
    # field on the form the app cannot validate against a vocabulary.
    course: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
