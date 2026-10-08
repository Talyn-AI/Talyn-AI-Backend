"""Mission models.

Missions come from two places and are deliberately split in two:

  * **MissionTemplate** — written by a creator, published to the catalogue,
    shared by every learner. Content only; nobody "completes" one.
  * **Mission** — a learner's adopted instance, with per-learner progress on
    its steps. Created by adopting a template, never written from scratch.

The split exists because progress cannot live on a shared object. A single
`completed` flag on a template's step would mean the first learner to finish
it completed it for everyone, so adopting necessarily copies the steps into
per-learner rows. `template_id` is kept on the instance so a creator can still
answer "how many learners took my mission".

Learner-authored missions from before this split still exist and keep
working: their `template_id` is simply NULL. Nothing is deleted or rewritten.
"""
from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class MissionTemplate(Base):
    """A creator-authored mission offered to all learners."""

    __tablename__ = "mission_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    creator_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(String(1000), default="")
    purpose: Mapped[str] = mapped_column(String(1000), default="")
    reward_xp: Mapped[int] = mapped_column(Integer, default=100)
    badge: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Unpublished templates are visible only to their creator, so a half-written
    # mission never appears in the learner's catalogue.
    published: Mapped[bool] = mapped_column(Boolean, default=False)

    steps: Mapped[list["MissionTemplateStep"]] = relationship(
        back_populates="template",
        cascade="all, delete-orphan",
        order_by="MissionTemplateStep.order",
    )

    creator: Mapped["User"] = relationship(back_populates="mission_templates")  # noqa: F821


class MissionTemplateStep(Base):
    __tablename__ = "mission_template_steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(
        ForeignKey("mission_templates.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(String(1000), default="")
    order: Mapped[int] = mapped_column(Integer)

    template: Mapped["MissionTemplate"] = relationship(back_populates="steps")


class Mission(Base):
    """One learner's adopted mission."""

    __tablename__ = "missions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # Which catalogue entry this was adopted from. NULL for missions the
    # learner wrote themselves before the catalogue existed, and also once a
    # creator withdraws the template they came from - the mission is the
    # learner's own copy and outlives the listing.
    template_id: Mapped[int | None] = mapped_column(
        ForeignKey("mission_templates.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Snapshot of the template's text at adoption time. Editing a published
    # template must not rewrite the wording of a mission someone is halfway
    # through — a learner working "Build a button" should not wake up to a
    # renamed objective.
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(String(1000), default="")
    purpose: Mapped[str] = mapped_column(String(1000), default="")
    reward_xp: Mapped[int] = mapped_column(Integer, default=100)
    badge: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="not_started")  # not_started | in_progress | completed

    steps: Mapped[list["MissionStep"]] = relationship(
        back_populates="mission", cascade="all, delete-orphan", order_by="MissionStep.order"
    )

    user: Mapped["User"] = relationship(back_populates="missions")  # noqa: F821


class MissionStep(Base):
    __tablename__ = "mission_steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    mission_id: Mapped[int] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(String(1000), default="")
    order: Mapped[int] = mapped_column(Integer)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)

    mission: Mapped["Mission"] = relationship(back_populates="steps")