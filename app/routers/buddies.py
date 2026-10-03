"""Buddy matches router: persist AI-ranked matches, accept/decline them."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.database import get_db
from app.models import BuddyMatch, User
from app.schemas.mission import (
    BUDDY_MATCH_STATUSES,
    BuddyMatchCreate,
    BuddyMatchRead,
    BuddyMatchStatusUpdate,
)

router = APIRouter(prefix="/me/buddies", tags=["Buddies"])


@router.post(
    "/matches", response_model=BuddyMatchRead, status_code=status.HTTP_201_CREATED
)
def save_match(
    payload: BuddyMatchCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BuddyMatch:
    """Save one AI-ranked buddy match for the learner."""
    if payload.buddy_user_id == current_user.id:
        raise HTTPException(status_code=422, detail="Cannot match with yourself")

    buddy = db.get(User, payload.buddy_user_id)
    if buddy is None:
        raise HTTPException(status_code=404, detail="Buddy user not found")

    existing = db.scalar(
        select(BuddyMatch).where(
            BuddyMatch.user_id == current_user.id,
            BuddyMatch.buddy_user_id == payload.buddy_user_id,
            BuddyMatch.status == "pending",
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="Pending match already exists")

    match = BuddyMatch(
        user_id=current_user.id,
        buddy_user_id=payload.buddy_user_id,
        match_score=payload.match_score,
        status="pending",
    )
    db.add(match)
    db.commit()
    db.refresh(match)
    return match


@router.get("/matches", response_model=list[BuddyMatchRead])
def list_matches(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[BuddyMatch]:
    """List the learner's saved buddy matches, newest first."""
    return list(
        db.scalars(
            select(BuddyMatch)
            .where(BuddyMatch.user_id == current_user.id)
            .order_by(BuddyMatch.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
    )


@router.patch("/matches/{match_id}")
def update_match_status(
    match_id: int,
    payload: BuddyMatchStatusUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Accept or decline a saved buddy match."""
    if payload.status not in BUDDY_MATCH_STATUSES:
        raise HTTPException(status_code=422, detail="Unknown match status")

    match = db.scalar(
        select(BuddyMatch).where(
            BuddyMatch.id == match_id, BuddyMatch.user_id == current_user.id
        )
    )
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")

    match.status = payload.status
    db.commit()
    return {"match_id": match_id, "status": match.status}
