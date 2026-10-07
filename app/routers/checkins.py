"""Daily check-ins: one tap that says "I showed up".

One row per learner per day (unique). The first check-in of the day awards
10 XP through the normal ledger, so check-ins extend the XP streak instead
of running a second streak system that could disagree with it. Repeating
the tap the same day returns the existing row with no further XP.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, require_onboarding
from app.database import get_db
from app.models import CheckIn, User
from app.schemas import CheckInIn, CheckInRead
from app.services.xp import award_xp

router = APIRouter(prefix="/me/check-ins", tags=["Progress"])

CHECKIN_XP = 10


@router.post("", response_model=CheckInRead)
def check_in(
    payload: CheckInIn,
    response: Response,
    current_user: User = Depends(require_onboarding),
    db: Session = Depends(get_db),
) -> CheckInRead:
    """Record today's check-in. 201 the first time, 200 on repeat taps."""
    today = datetime.now(timezone.utc).date()
    existing = db.scalar(
        select(CheckIn).where(
            CheckIn.user_id == current_user.id,
            CheckIn.check_date == today,
        )
    )
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return _read(existing, xp_awarded=0)

    row = CheckIn(
        user_id=current_user.id,
        check_date=today,
        mood=payload.mood,
        note=payload.note,
    )
    db.add(row)
    # Activity "streak", not a new "checkin" activity: the coach validates
    # xp_breakdown against a closed enum, and an unknown activity would fail
    # every coach call for anyone who ever checked in. The 20 XP milestone
    # amount is deliberately overridden — a daily tap is worth 10.
    award_xp(db, current_user, "streak", amount=CHECKIN_XP,
             note="Daily check-in")
    db.commit()
    db.refresh(row)
    response.status_code = status.HTTP_201_CREATED
    return _read(row, xp_awarded=CHECKIN_XP)


@router.get("", response_model=list[CheckInRead])
def check_in_history(
    days: int = 30,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CheckInRead]:
    """Recent check-ins, newest first. Powers the Progress page calendar."""
    days = max(1, min(days, 90))
    rows = db.scalars(
        select(CheckIn)
        .where(CheckIn.user_id == current_user.id)
        .order_by(CheckIn.check_date.desc())
        .limit(days)
    ).all()
    return [_read(row, xp_awarded=0) for row in rows]


def _read(row: CheckIn, xp_awarded: int) -> CheckInRead:
    return CheckInRead(
        date=row.check_date,
        mood=row.mood,
        note=row.note,
        xp_awarded=xp_awarded,
    )
