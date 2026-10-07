"""LearnerMaterial: a private study document in a learner's library."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class LearnerMaterial(Base):
    """One claimed upload. Private to its owner: no course, no sharing, no
    moderation queue — the library is a shelf, not a publication."""

    __tablename__ = "learner_materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255), default="")
    storage_key: Mapped[str] = mapped_column(String(500), unique=True)
    content_type: Mapped[str] = mapped_column(String(120), default="")
    # Verified against the storage provider at claim time. Never the client's.
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    # clean | unscanned | infected | failed — same vocabulary as lesson assets.
    scan_status: Mapped[str] = mapped_column(String(20), default="unscanned")
    scan_detail: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
