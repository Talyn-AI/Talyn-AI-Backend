"""Missions: a learner adopts creator-written missions and works through them.

Creating a mission is a creator action (app/routers/creator_missions.py). This
router covers what a learner does with one: browse the catalogue, adopt, track
steps, complete, drop.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.deps import get_current_user, require_onboarding, optional_user
from app.database import get_db
from app.models import Badge, Mission, MissionStep, MissionTemplate, User
from app.schemas.mission import (
    MISSION_STATUSES,
    MissionAdopt,
    MissionCatalogRead,
    MissionRead,
    MissionStatusUpdate,
)
from app.services.xp import award_xp

router = APIRouter(prefix="/me/missions", tags=["Missions"])
catalog_router = APIRouter(prefix="/missions", tags=["Missions"])


def _complete_mission(db: Session, user: User, mission: Mission) -> dict:
    """Mark a mission completed (idempotent): awards XP once + badge if set."""
    if mission.status == "completed":
        return {"completed": True, "already_completed": True, "xp_awarded": 0}

    mission.status = "completed"
    xp = award_xp(
        db,
        user,
        "mission",
        note=f"Completed mission: {mission.title}",
        amount=mission.reward_xp,
    )
    badge_awarded = None
    if mission.badge:
        badge_id = f"mission-{mission.id}"
        exists = db.scalar(select(Badge).where(Badge.badge_id == badge_id))
        if exists is None:
            db.add(
                Badge(
                    user_id=user.id,
                    badge_id=badge_id,
                    name=mission.badge,
                    description=f"Earned by completing: {mission.title}",
                )
            )
            badge_awarded = mission.badge
    db.commit()
    return {
        "completed": True,
        "already_completed": False,
        "xp_awarded": xp.amount,
        "badge_awarded": badge_awarded,
    }


def _get_owned_mission(db: Session, user: User, mission_id: int) -> Mission:
    mission = db.scalar(
        select(Mission)
        .where(Mission.id == mission_id, Mission.user_id == user.id)
        .options(selectinload(Mission.steps))
    )
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    return mission


@catalog_router.get("", response_model=list[MissionCatalogRead])
def browse_catalogue(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    viewer: User | None = Depends(optional_user),
    db: Session = Depends(get_db),
) -> list[MissionCatalogRead]:
    """Published missions available to adopt.

    Public on purpose: the catalogue is how a learner decides what to learn
    next, and browsing it should not require an account. Unpublished templates
    never appear here.
    """
    templates = list(
        db.scalars(
            select(MissionTemplate)
            .where(MissionTemplate.published.is_(True))
            .options(selectinload(MissionTemplate.steps))
            .order_by(MissionTemplate.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
    )

    # One query for everything the viewer has already adopted, rather than one
    # per template: this list is rendered as a grid and N+1 would show.
    mine: dict[int, int] = {}
    if viewer is not None and templates:
        rows = db.execute(
            select(Mission.template_id, Mission.id).where(
                Mission.user_id == viewer.id,
                Mission.template_id.in_([t.id for t in templates]),
            )
        ).all()
        mine = {tid: mid for tid, mid in rows if tid is not None}

    out = []
    for template in templates:
        creator = db.get(User, template.creator_user_id)
        adopted_id = mine.get(template.id)
        out.append(
            MissionCatalogRead(
                **{
                    field: getattr(template, field)
                    for field in (
                        "id", "title", "description", "purpose",
                        "reward_xp", "badge", "published",
                    )
                },
                steps=template.steps,
                creator_name=(creator.learner_name if creator else ""),
                adopted=adopted_id is not None,
                adopted_mission_id=adopted_id,
            )
        )
    return out


@router.post("", response_model=MissionRead, status_code=status.HTTP_201_CREATED)
def adopt_mission(
    payload: MissionAdopt,
    current_user: User = Depends(require_onboarding),
    db: Session = Depends(get_db),
) -> Mission:
    """Adopt a published mission from the catalogue.

    Copies the template's content rather than linking to it, because progress
    is per learner: two learners on the same mission must not share step
    completion. The copy also means editing the template later cannot rewrite
    a mission someone is already halfway through.

    One active mission at a time — the product treats a mission as the single
    thing a learner is working on, which is what makes "finish or complete
    mission N first" a useful nudge rather than an obstacle.
    """
    template = db.scalar(
        select(MissionTemplate)
        .where(MissionTemplate.id == payload.template_id,
               MissionTemplate.published.is_(True))
        .options(selectinload(MissionTemplate.steps))
    )
    if template is None:
        # 404 covers both "no such template" and "not published": a learner
        # should not be able to discover drafts by guessing ids.
        raise HTTPException(status_code=404, detail="Mission not available")

    active = db.scalar(
        select(Mission).where(
            Mission.user_id == current_user.id, Mission.status == "in_progress"
        )
    )
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Finish or complete mission {active.id} first",
        )

    already = db.scalar(
        select(Mission).where(
            Mission.user_id == current_user.id, Mission.template_id == template.id
        )
    )
    if already is not None:
        raise HTTPException(
            status_code=409,
            detail="You have already taken this mission",
        )

    mission = Mission(
        user_id=current_user.id,
        template_id=template.id,
        title=template.title,
        description=template.description,
        purpose=template.purpose,
        reward_xp=template.reward_xp,
        badge=template.badge,
        status="in_progress",
    )
    for step in template.steps:
        mission.steps.append(
            MissionStep(
                title=step.title, description=step.description, order=step.order
            )
        )
    db.add(mission)
    db.commit()
    db.refresh(mission)
    return mission


@router.get("", response_model=list[MissionRead])
def list_missions(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Mission]:
    """List the learner's adopted missions, optionally filtered by status."""
    query = (
        select(Mission)
        .where(Mission.user_id == current_user.id)
        .options(selectinload(Mission.steps))
        .order_by(Mission.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if status_filter is not None:
        if status_filter not in MISSION_STATUSES:
            raise HTTPException(status_code=422, detail="Unknown mission status")
        query = query.where(Mission.status == status_filter)
    return list(db.scalars(query).all())


@router.get("/{mission_id}", response_model=MissionRead)
def get_mission(
    mission_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Mission:
    """Get one of the learner's missions with its steps."""
    return _get_owned_mission(db, current_user, mission_id)


@router.patch("/{mission_id}")
def update_mission_status(
    mission_id: int,
    payload: MissionStatusUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Change a mission's status. Setting completed awards XP + badge."""
    if payload.status not in MISSION_STATUSES:
        raise HTTPException(status_code=422, detail="Unknown mission status")

    mission = _get_owned_mission(db, current_user, mission_id)
    if payload.status == "completed":
        return {"mission_id": mission_id, **_complete_mission(db, current_user, mission)}

    mission.status = payload.status
    db.commit()
    return {"mission_id": mission_id, "status": mission.status}


@router.post("/{mission_id}/steps/{step_id}/complete")
def complete_step(
    mission_id: int,
    step_id: int,
    current_user: User = Depends(require_onboarding),
    db: Session = Depends(get_db),
) -> dict:
    """Complete one mission step. Finishing the last step completes the mission."""
    mission = _get_owned_mission(db, current_user, mission_id)
    step = next((s for s in mission.steps if s.id == step_id), None)
    if step is None:
        raise HTTPException(status_code=404, detail="Step not found in this mission")
    if mission.status == "completed":
        raise HTTPException(status_code=409, detail="Mission already completed")

    step.completed = True
    if all(s.completed for s in mission.steps) and mission.steps:
        result = {"step_id": step_id, "step_completed": True}
        result.update(_complete_mission(db, current_user, mission))
        return result

    db.commit()
    remaining = sum(1 for s in mission.steps if not s.completed)
    return {"step_id": step_id, "step_completed": True, "steps_remaining": remaining}


@router.delete("/{mission_id}")
def delete_mission(
    mission_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Delete one of the learner's missions with its steps."""
    mission = _get_owned_mission(db, current_user, mission_id)
    db.delete(mission)
    db.commit()
    return {"message": f"Mission {mission_id} deleted"}
