"""Creator-authored missions: the catalogue learners choose from.

Learners cannot write missions. They browse what creators published and adopt
one, which copies it into a per-learner instance so progress stays per
learner (see app/models/mission.py for why that copy is necessary).

A creator owns their templates outright: every route re-checks ownership
rather than trusting a filter, and someone else's template answers 404 rather
than 403 so this endpoint cannot be used to enumerate other creators' ids.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.deps import require_creator
from app.database import get_db
from app.models import Mission, MissionTemplate, MissionTemplateStep, User
from app.schemas.mission import (
    MissionStepCreate,
    MissionTemplateCreate,
    MissionTemplateRead,
    MissionTemplateUpdate,
)

router = APIRouter(prefix="/creator/missions", tags=["Creator missions"])


def _replace_steps(template: MissionTemplate, steps: list[MissionStepCreate]) -> None:
    """Swap a template's step list wholesale.

    Safe because a template is a proposal, not a progress record. Learners who
    already adopted the mission hold their own copy of these rows, so nothing
    anyone is halfway through is affected.
    """
    template.steps.clear()
    for step in steps:
        template.steps.append(
            MissionTemplateStep(
                title=step.title, description=step.description, order=step.order
            )
        )


def _owned(db: Session, creator: User, template_id: int) -> MissionTemplate:
    template = db.scalar(
        select(MissionTemplate)
        .where(
            MissionTemplate.id == template_id,
            MissionTemplate.creator_user_id == creator.id,
        )
        .options(selectinload(MissionTemplate.steps))
    )
    if template is None:
        raise HTTPException(status_code=404, detail="Mission template not found")
    return template


@router.post("", response_model=MissionTemplateRead, status_code=status.HTTP_201_CREATED)
def create_template(
    payload: MissionTemplateCreate,
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> MissionTemplate:
    """Create a mission template. Starts unpublished — set `published` to list it."""
    template = MissionTemplate(
        creator_user_id=creator.id,
        title=payload.title,
        description=payload.description,
        purpose=payload.purpose,
        reward_xp=payload.reward_xp,
        badge=payload.badge,
        published=False,
    )
    _replace_steps(template, payload.steps)
    db.add(template)
    db.commit()
    db.refresh(template)
    return template


@router.get("", response_model=list[MissionTemplateRead])
def list_my_templates(
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> list[MissionTemplate]:
    """Every template the signed-in creator has written, published or not."""
    return list(
        db.scalars(
            select(MissionTemplate)
            .where(MissionTemplate.creator_user_id == creator.id)
            .options(selectinload(MissionTemplate.steps))
            .order_by(MissionTemplate.id.desc())
        ).all()
    )


@router.get("/{template_id}", response_model=MissionTemplateRead)
def get_template(
    template_id: int,
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> MissionTemplate:
    """One of the creator's own templates."""
    return _owned(db, creator, template_id)


@router.patch("/{template_id}", response_model=MissionTemplateRead)
def update_template(
    template_id: int,
    payload: MissionTemplateUpdate,
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> MissionTemplate:
    """Edit a template.

    Existing adopted missions keep the wording they were adopted with: a
    learner part-way through "Build a button" should not find it renamed
    tomorrow. Only later adopters see the new text.
    """
    template = _owned(db, creator, template_id)

    for field in ("title", "description", "purpose", "reward_xp", "badge",
                  "published"):
        value = getattr(payload, field)
        if value is not None:
            setattr(template, field, value)

    if payload.steps is not None:
        _replace_steps(template, payload.steps)

    db.commit()
    db.refresh(template)
    return template


@router.delete("/{template_id}")
def delete_template(
    template_id: int,
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> dict:
    """Withdraw a template from the catalogue.

    Learners who already adopted it keep their mission, because adoption
    copies the content — nobody's work in progress is cancelled.
    """
    template = _owned(db, creator, template_id)
    db.delete(template)
    db.commit()
    return {"message": f"Mission template {template_id} deleted"}


@router.get("/{template_id}/adoption")
def adoption_stats(
    template_id: int,
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> dict:
    """How many learners took this mission, and how many finished it."""
    _owned(db, creator, template_id)
    adopted = db.scalar(
        select(func.count(Mission.id)).where(Mission.template_id == template_id)
    )
    completed = db.scalar(
        select(func.count(Mission.id)).where(
            Mission.template_id == template_id, Mission.status == "completed"
        )
    )
    return {
        "template_id": template_id,
        "adopted": adopted or 0,
        "completed": completed or 0,
    }