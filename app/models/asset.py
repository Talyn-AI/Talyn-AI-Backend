"""LessonAsset: a video, resource file, or external link on a lesson.

`size_bytes` is the size the *provider* reported, not what the uploader
claimed — see services/uploads.py. `scan_status` records what the malware
scanner concluded, so an operator can tell a clean file from one that was
never looked at.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

KIND_VIDEO = "video"
KIND_RESOURCE = "resource"
KIND_LINK = "link"
ASSET_KINDS = {KIND_VIDEO, KIND_RESOURCE, KIND_LINK}


class LessonAsset(Base):
    __tablename__ = "lesson_assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    lesson_id: Mapped[int] = mapped_column(ForeignKey("lessons.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # video | resource | link
    storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    filename: Mapped[str] = mapped_column(String(255), default="")
    # Verified against the storage provider on attach. 0 for links.
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    # clean | unscanned | infected | failed
    scan_status: Mapped[str] = mapped_column(String(20), default="unscanned")
    scan_detail: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    lesson: Mapped["Lesson"] = relationship(back_populates="assets")  # noqa: F821
