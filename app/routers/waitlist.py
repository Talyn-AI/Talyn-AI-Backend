"""Waitlist: signups from the early-access page, before an account exists."""
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import WaitlistSignup
from app.schemas import WaitlistJoined, WaitlistSignupIn

router = APIRouter(prefix="/waitlist", tags=["Waitlist"])

JOINED_MESSAGE = "You're on the list — we'll be in touch when your place opens."


@router.post("", response_model=WaitlistJoined, status_code=status.HTTP_201_CREATED)
def join_waitlist(
    payload: WaitlistSignupIn,
    response: Response,
    db: Session = Depends(get_db),
) -> WaitlistJoined:
    """Add someone to the early-access waitlist. No auth — they have no account.

    Repeat submissions update the existing row rather than creating a second
    one, so someone who fixes a typo and resubmits is not counted twice. The
    status code is the only thing that differs: 201 the first time, 200 after.

    Deliberately does not send a confirmation email. Mail is only configured
    for one address, so every other signup would see a bounce and conclude the
    form is broken — a worse outcome than no mail at all.
    """
    # Lowercased before both the lookup and the write. EmailStr normalises the
    # domain but leaves the local part as typed, so "Ada@example.com" and
    # "ada@example.com" would otherwise become two rows for one person — and
    # they would both claim a place on the list.
    email = str(payload.email).strip().lower()

    existing = db.scalar(
        select(WaitlistSignup).where(WaitlistSignup.email == email)
    )

    if existing is None:
        signup = WaitlistSignup(
            email=email,
            name=payload.name.strip(),
            role=payload.role,
            interests=payload.interests,
            course=payload.course.strip(),
        )
        db.add(signup)
        db.flush()
        created = True
    else:
        signup = existing
        signup.name = payload.name.strip()
        signup.role = payload.role
        signup.interests = payload.interests
        signup.course = payload.course.strip()
        created = False

    db.commit()
    db.refresh(signup)

    if not created:
        response.status_code = status.HTTP_200_OK

    # Count by serial id rather than created_at: ids are assigned in insertion
    # order, so this is the position they joined at and does not drift when
    # timestamps collide or the table grows.
    position = int(
        db.scalar(
            select(func.count(WaitlistSignup.id)).where(
                WaitlistSignup.id <= signup.id
            )
        )
        or 0
    )

    return WaitlistJoined(
        message=JOINED_MESSAGE,
        email=signup.email,
        position=position,
        created=created,
    )
