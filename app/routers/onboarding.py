"""Onboarding: the steps between a new account and a usable one.

Order matters and is enforced here rather than trusted to the client:

    verify email  ->  choose pace  ->  choose interests

Completing onboarding also seeds the study plan, so the account arrives with a
daily goal derived from the pace rather than the same default 30 minutes
everyone would otherwise get.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.core.onboarding import (
    DEFAULT_PACE,
    INTERESTS,
    LEARNING_PACES,
    PACE_BY_KEY,
    WEEKLY_TARGET_BY_PACE,
)
from app.database import get_db
from app.models import StudyPlan, User
from app.schemas.onboarding import (
    InterestOptionOut,
    OnboardingCompleteIn,
    OnboardingOptionsOut,
    OnboardingStatusOut,
    PaceOptionOut,
)

router = APIRouter(prefix="/onboarding", tags=["Onboarding"])


def _next_step(user: User) -> str:
    """Which screen the client should show, in one place.

    Returned rather than left to the client so two implementations cannot
    disagree about whether verification comes before pace.
    """
    if user.email_verified_at is None:
        return "verify_email"
    if user.learning_pace is None:
        return "choose_pace"
    if not user.interests:
        return "choose_interests"
    if user.onboarding_completed_at is None:
        return "complete"
    return "done"


@router.get("/options", response_model=OnboardingOptionsOut)
def onboarding_options() -> OnboardingOptionsOut:
    """Pace and interest options, server-side.

    The pickers read this rather than hardcoding a list: a client with its own
    copy will eventually offer something the API rejects.
    """
    return OnboardingOptionsOut(
        paces=[
            PaceOptionOut(
                key=p.key,
                label=p.label,
                min_minutes=p.min_minutes,
                max_minutes=p.max_minutes,
                daily_goal_minutes=p.daily_goal_minutes,
                description=p.description,
            )
            for p in LEARNING_PACES
        ],
        interests=[InterestOptionOut(key=k, label=label) for k, label in INTERESTS],
        default_pace=DEFAULT_PACE,
    )


@router.get("/status", response_model=OnboardingStatusOut)
def onboarding_status(
    current_user: User = Depends(get_current_user),
) -> OnboardingStatusOut:
    """Where this account is in onboarding, and what comes next."""
    return OnboardingStatusOut(
        email_verified=current_user.email_verified_at is not None,
        onboarding_completed=current_user.onboarding_completed_at is not None,
        learning_pace=current_user.learning_pace,
        interests=list(current_user.interests or []),
        next_step=_next_step(current_user),
    )


@router.post("/complete", response_model=OnboardingStatusOut)
def complete_onboarding(
    payload: OnboardingCompleteIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OnboardingStatusOut:
    """Record pace and interests, and seed the study plan.

    Requires a verified address: pace and interests are personal choices about
    how a specific person learns, and letting an unproven address set them
    would mean anyone who can be spammed into signing up can shape someone's
    plan.

    Repeatable — changing your pace later is normal, and an account that had to
    ask a support question to change a setting would be a support question.
    """
    if current_user.email_verified_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Confirm your email address before finishing setup",
        )

    current_user.learning_pace = payload.learning_pace
    current_user.interests = payload.interests
    current_user.onboarding_completed_at = datetime.now(timezone.utc)

    pace = PACE_BY_KEY[payload.learning_pace]
    plan = db.scalar(select(StudyPlan).where(StudyPlan.user_id == current_user.id))
    if plan is None:
        plan = StudyPlan(user_id=current_user.id)
        db.add(plan)
    plan.daily_goal_minutes = pace.daily_goal_minutes
    plan.weekly_target_lessons = WEEKLY_TARGET_BY_PACE.get(
        pace.key, plan.weekly_target_lessons
    )

    db.commit()
    db.refresh(current_user)

    return OnboardingStatusOut(
        email_verified=True,
        onboarding_completed=True,
        learning_pace=current_user.learning_pace,
        interests=list(current_user.interests or []),
        next_step="done",
    )