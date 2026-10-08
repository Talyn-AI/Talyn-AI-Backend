"""CreatorProfile: public creator identity shown on course pages."""
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CreatorProfile(Base):
    __tablename__ = "creator_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    display_name: Mapped[str] = mapped_column(String(120))
    bio: Mapped[str] = mapped_column(String(1000), default="")
    image_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
